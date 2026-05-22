"""Outputs / table-lookup stack for the Live Call Wall.

AppSync now lives in `lambda_stack.py` because it grants + env-wires the
stream_to_appsync Lambda directly (splitting them caused a cycle). This stack
kept for parity with cdk_app.py — exposes a few CFN outputs so operators can
read durable values without describing the LambdaStack.
"""
from __future__ import annotations

import aws_cdk as cdk
from constructs import Construct

from stacks.data_stack import DataStack
from stacks.lambda_stack import LambdaStack


class ApiStack(cdk.Stack):
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

        cdk.CfnOutput(self, "CallsTableName", value=data.calls_table.table_name)
        cdk.CfnOutput(self, "BookingsTableName", value=data.bookings_table.table_name)
        cdk.CfnOutput(self, "WallApiLambdaName", value=lambdas.wall_api.function_name)
        cdk.CfnOutput(self, "InputAgentLambdaName", value=lambdas.input_agent.function_name)
