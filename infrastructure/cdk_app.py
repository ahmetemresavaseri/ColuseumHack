"""Atrium CDK app.

The stack split mirrors README.md so infra, data/RAG, API, and Lambda owners can
work in parallel behind stable stack boundaries.
"""
from __future__ import annotations

import os

import aws_cdk as cdk

from stacks.api_stack import ApiStack
from stacks.data_stack import DataStack
from stacks.lambda_stack import LambdaStack
from stacks.rag_stack import RagStack


app = cdk.App()

env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", os.environ.get("AWS_REGION", "us-west-2")),
)

data = DataStack(app, "AtriumData", env=env)
rag = RagStack(app, "AtriumRag", env=env)
lambdas = LambdaStack(app, "AtriumLambdas", env=env, data=data, rag=rag)
ApiStack(app, "AtriumApi", env=env, data=data, lambdas=lambdas)

app.synth()
