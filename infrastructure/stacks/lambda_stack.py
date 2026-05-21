"""Lambda resources for the live call path."""
from __future__ import annotations

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import aws_lambda_event_sources as event_sources
from aws_cdk import aws_lambda as lambda_
from constructs import Construct

from stacks.data_stack import DataStack
from stacks.rag_stack import RagStack

ROOT = Path(__file__).resolve().parents[2]


class LambdaStack(cdk.Stack):
    """Input Agent, Brain, AppSync stream, and Wall API Lambdas."""

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

        self.input_agent = self._python_lambda(
            "InputAgent",
            ROOT / "lambdas" / "input_agent",
            "handler.lambda_handler",
            timeout=cdk.Duration.seconds(8),
        )
        self.brain = self._python_lambda("Brain", ROOT / "lambdas" / "brain", "handler.handler")
        self.stream_to_appsync = self._python_lambda(
            "StreamToAppSync",
            ROOT / "lambdas" / "stream_to_appsync",
            "handler.handler",
        )
        self.wall_api = self._python_lambda("WallApi", ROOT / "lambdas" / "wall_api", "handler.handler")

        for table in [
            data.calls_table,
            data.bookings_table,
            data.companies_table,
            data.crews_table,
            data.price_matrix_table,
        ]:
            table.grant_read_write_data(self.input_agent)
            table.grant_read_data(self.brain)
            table.grant_read_data(self.wall_api)

        rag.kb_bucket.grant_read(self.input_agent)
        rag.recordings_bucket.grant_read_write(self.input_agent)
        self.brain.grant_invoke(self.input_agent)

        self.stream_to_appsync.add_event_source(
            event_sources.DynamoEventSource(
                data.calls_table,
                starting_position=lambda_.StartingPosition.LATEST,
                batch_size=10,
            )
        )
        self.stream_to_appsync.add_event_source(
            event_sources.DynamoEventSource(
                data.bookings_table,
                starting_position=lambda_.StartingPosition.LATEST,
                batch_size=10,
            )
        )

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
