"""Bedrock Knowledge Base source storage for tenant RAG documents."""
from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_s3 as s3
from constructs import Construct


class RagStack(cdk.Stack):
    """S3 source buckets for Bedrock KB ingestion.

    Bedrock Knowledge Base and S3 Vectors resources may need to be finalized
    manually or through L1 constructs depending on account/region support.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.kb_bucket = s3.Bucket(
            self,
            "KbBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=cdk.RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        self.recordings_bucket = s3.Bucket(
            self,
            "RecordingsBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=cdk.RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )
