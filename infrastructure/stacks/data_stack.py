"""DynamoDB and storage resources for Atrium."""
from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_dynamodb as dynamodb
from constructs import Construct


class DataStack(cdk.Stack):
    """Tables described in README.md > Data Model."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.calls_table = self._table(
            "Calls",
            partition_key="callId",
            sort_key="sk",
            stream=True,
        )
        self.bookings_table = self._table(
            "Bookings",
            partition_key="bookingId",
            sort_key="sk",
            stream=True,
        )
        self.companies_table = self._table("Companies", partition_key="companyId")
        self.crews_table = self._table("Crews", partition_key="companyId", sort_key="crewId")
        self.price_matrix_table = self._table(
            "PriceMatrix",
            partition_key="companyId",
            sort_key="serviceType",
        )

        self.bookings_table.add_global_secondary_index(
            index_name="company-updatedAt",
            partition_key=dynamodb.Attribute(
                name="companyId",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="updatedAt",
                type=dynamodb.AttributeType.STRING,
            ),
        )

    def _table(
        self,
        name: str,
        partition_key: str,
        sort_key: str | None = None,
        stream: bool = False,
    ) -> dynamodb.Table:
        props = {
            "table_name": f"atrium-{name.lower()}",
            "partition_key": dynamodb.Attribute(
                name=partition_key,
                type=dynamodb.AttributeType.STRING,
            ),
            "billing_mode": dynamodb.BillingMode.PAY_PER_REQUEST,
            "removal_policy": cdk.RemovalPolicy.DESTROY,
        }
        if stream:
            props["stream"] = dynamodb.StreamViewType.NEW_AND_OLD_IMAGES
        if sort_key:
            props["sort_key"] = dynamodb.Attribute(
                name=sort_key,
                type=dynamodb.AttributeType.STRING,
            )
        return dynamodb.Table(self, name, **props)
