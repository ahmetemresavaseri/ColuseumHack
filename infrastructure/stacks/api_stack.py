"""AppSync and API-facing resources for the Live Call Wall."""
from __future__ import annotations

import aws_cdk as cdk
from constructs import Construct

from stacks.data_stack import DataStack
from stacks.lambda_stack import LambdaStack


class ApiStack(cdk.Stack):
    """Placeholder boundary for AppSync subscriptions and wall queries."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        data: DataStack,
        lambdas: LambdaStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        cdk.CfnOutput(self, "WallApiLambdaName", value=lambdas.wall_api.function_name)
        cdk.CfnOutput(self, "CallsTableName", value=data.calls_table.table_name)
