"""Lambda + AppSync resources for the live call path.

AppSync lives in this stack rather than `api_stack.py` because it needs to
grant + env-wire the `stream_to_appsync` Lambda; splitting them across stacks
created a dependency cycle (LambdaStack → ApiStack → LambdaStack via env
vars/IAM grants).
"""
from __future__ import annotations

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import aws_appsync as appsync
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as events_targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda_event_sources as event_sources
from aws_cdk import aws_lambda as lambda_
from constructs import Construct

from stacks.data_stack import DataStack
from stacks.rag_stack import RagStack

ROOT = Path(__file__).resolve().parents[2]


SCHEMA = """
type WallEvent @aws_iam @aws_api_key {
  callId: ID!
  companyId: ID!
  timestamp: AWSDateTime!
  type: String!
  payload: AWSJSON
}

input WallEventInput {
  callId: ID!
  companyId: ID!
  timestamp: AWSDateTime!
  type: String!
  payload: AWSJSON
}

type Query {
  ping: String
}

type Mutation {
  publishWallEvent(event: WallEventInput!): WallEvent
    @aws_iam @aws_api_key
}

type Subscription {
  onWallEvent(companyId: ID!): WallEvent
    @aws_subscribe(mutations: ["publishWallEvent"])
    @aws_api_key
}
"""

PUBLISH_REQUEST_MAPPING = """
{
  "version": "2017-02-28",
  "payload": {
    "callId":     "$ctx.args.event.callId",
    "companyId":  "$ctx.args.event.companyId",
    "timestamp":  "$ctx.args.event.timestamp",
    "type":       "$ctx.args.event.type",
    "payload":    $util.toJson($ctx.args.event.payload)
  }
}
"""

PUBLISH_RESPONSE_MAPPING = "$util.toJson($ctx.result)"
PING_REQUEST_MAPPING = '{"version":"2017-02-28","payload":"pong"}'
PING_RESPONSE_MAPPING = "$util.toJson($ctx.result)"


class LambdaStack(cdk.Stack):
    """Input Agent (Connect/Lex), Brain, Wall API, AppSync fan-out + GraphQL."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        data: DataStack,
        rag: RagStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Lambdas ----------------------------------------------------------
        self.input_agent = self._python_lambda(
            "InputAgent",
            ROOT / "lambdas" / "input_agent",
            "handler.lambda_handler",
            timeout=cdk.Duration.seconds(8),
        )
        self.brain = self._python_lambda(
            "Brain",
            ROOT / "lambdas" / "brain",
            "handler.handler",
            timeout=cdk.Duration.seconds(10),
        )
        self.stream_to_appsync = self._python_lambda(
            "StreamToAppSync",
            ROOT / "lambdas" / "stream_to_appsync",
            "handler.handler",
            timeout=cdk.Duration.seconds(15),
        )
        self.wall_api = self._python_lambda(
            "WallApi", ROOT / "lambdas" / "wall_api", "handler.handler"
        )

        # Table grants -----------------------------------------------------
        for table in [
            data.calls_table,
            data.bookings_table,
            data.companies_table,
            data.crews_table,
            data.price_matrix_table,
            data.knowledge_items_table,
        ]:
            table.grant_read_write_data(self.input_agent)
            table.grant_read_data(self.brain)
            table.grant_read_data(self.wall_api)

        for fn in (self.input_agent, self.brain, self.wall_api, self.stream_to_appsync):
            fn.add_environment("CALLS_TABLE", data.calls_table.table_name)
            fn.add_environment("BOOKINGS_TABLE", data.bookings_table.table_name)
            fn.add_environment("COMPANIES_TABLE", data.companies_table.table_name)
            fn.add_environment("CREWS_TABLE", data.crews_table.table_name)
            fn.add_environment("PRICE_MATRIX_TABLE", data.price_matrix_table.table_name)
            fn.add_environment("KNOWLEDGE_ITEMS_TABLE", data.knowledge_items_table.table_name)

        self.input_agent.add_environment("DDB_BACKEND", "real")
        self.input_agent.add_environment("BRAIN_FUNCTION_NAME", self.brain.function_name)
        self.input_agent.add_environment("BEDROCK_KB_ID", "")
        rag.kb_bucket.grant_read(self.input_agent)
        rag.recordings_bucket.grant_read_write(self.input_agent)
        self.brain.grant_invoke(self.input_agent)

        # AppSync GraphQL API ---------------------------------------------
        self.graphql_api = appsync.CfnGraphQLApi(
            self,
            "WallGraphQLApi",
            name="atrium-wall",
            authentication_type="API_KEY",
            additional_authentication_providers=[
                appsync.CfnGraphQLApi.AdditionalAuthenticationProviderProperty(
                    authentication_type="AWS_IAM",
                )
            ],
        )
        self.api_key = appsync.CfnApiKey(
            self,
            "WallApiKey",
            api_id=self.graphql_api.attr_api_id,
            description="Atrium wall API key",
        )
        schema = appsync.CfnGraphQLSchema(
            self,
            "WallSchema",
            api_id=self.graphql_api.attr_api_id,
            definition=SCHEMA,
        )
        none_ds = appsync.CfnDataSource(
            self,
            "NoneDataSource",
            api_id=self.graphql_api.attr_api_id,
            name="NoneDS",
            type="NONE",
        )
        publish_resolver = appsync.CfnResolver(
            self,
            "PublishWallEventResolver",
            api_id=self.graphql_api.attr_api_id,
            type_name="Mutation",
            field_name="publishWallEvent",
            data_source_name=none_ds.name,
            request_mapping_template=PUBLISH_REQUEST_MAPPING,
            response_mapping_template=PUBLISH_RESPONSE_MAPPING,
        )
        publish_resolver.add_dependency(schema)
        publish_resolver.add_dependency(none_ds)
        ping_resolver = appsync.CfnResolver(
            self,
            "PingResolver",
            api_id=self.graphql_api.attr_api_id,
            type_name="Query",
            field_name="ping",
            data_source_name=none_ds.name,
            request_mapping_template=PING_REQUEST_MAPPING,
            response_mapping_template=PING_RESPONSE_MAPPING,
        )
        ping_resolver.add_dependency(schema)
        ping_resolver.add_dependency(none_ds)

        self.stream_to_appsync.add_to_role_policy(
            iam.PolicyStatement(
                actions=["appsync:GraphQL"],
                resources=[
                    f"arn:aws:appsync:{self.region}:{self.account}:apis/{self.graphql_api.attr_api_id}/types/Mutation/fields/publishWallEvent",
                ],
            )
        )
        self.stream_to_appsync.add_environment(
            "APPSYNC_GRAPHQL_URL", self.graphql_api.attr_graph_ql_url
        )
        self.stream_to_appsync.add_environment(
            "APPSYNC_API_ID", self.graphql_api.attr_api_id
        )

        # DynamoDB Streams -------------------------------------------------
        self.stream_to_appsync.add_event_source(
            event_sources.DynamoEventSource(
                data.calls_table,
                starting_position=lambda_.StartingPosition.LATEST,
                batch_size=10,
                retry_attempts=2,
            )
        )
        self.stream_to_appsync.add_event_source(
            event_sources.DynamoEventSource(
                data.bookings_table,
                starting_position=lambda_.StartingPosition.LATEST,
                batch_size=10,
                retry_attempts=2,
            )
        )

        # Warmers ----------------------------------------------------------
        # Caller-perceived latency on the first turn is dominated by Lambda
        # cold start (init ~400ms + first invoke time). Fire a no-op invoke
        # every 4 minutes on the call-path Lambdas to keep them warm; the
        # handlers short-circuit when `event.warmer == True`.
        warmer_schedule = events.Schedule.rate(cdk.Duration.minutes(4))
        warmer_payload = events.RuleTargetInput.from_object({"warmer": True})

        warmer_input = events.Rule(
            self, "InputAgentWarmer",
            schedule=warmer_schedule,
            description="Keep the Input Agent Lambda warm for low first-turn latency.",
        )
        warmer_input.add_target(events_targets.LambdaFunction(
            self.input_agent, event=warmer_payload,
        ))

        warmer_brain = events.Rule(
            self, "BrainWarmer",
            schedule=warmer_schedule,
            description="Keep the Brain Lambda warm so the live estimate fires fast.",
        )
        warmer_brain.add_target(events_targets.LambdaFunction(
            self.brain, event=warmer_payload,
        ))

        # Outputs ----------------------------------------------------------
        cdk.CfnOutput(self, "WallGraphQLUrl", value=self.graphql_api.attr_graph_ql_url)
        cdk.CfnOutput(self, "WallApiKeyValue", value=self.api_key.attr_api_key)
        cdk.CfnOutput(self, "WallApiId", value=self.graphql_api.attr_api_id)
        cdk.CfnOutput(self, "WallApiLambdaName", value=self.wall_api.function_name)
        cdk.CfnOutput(self, "InputAgentLambdaName", value=self.input_agent.function_name)

    def _python_lambda(
        self,
        name: str,
        path: Path,
        handler: str,
        timeout: cdk.Duration | None = None,
    ) -> lambda_.Function:
        return lambda_.Function(
            self,
            name,
            runtime=lambda_.Runtime.PYTHON_3_13,
            code=lambda_.Code.from_asset(str(path)),
            handler=handler,
            memory_size=1024,
            timeout=timeout or cdk.Duration.seconds(30),
        )
