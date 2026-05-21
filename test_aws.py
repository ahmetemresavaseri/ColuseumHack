import boto3
from botocore.exceptions import NoCredentialsError, ClientError

try:
    sts = boto3.client("sts")
    identity = sts.get_caller_identity()
    print("AWS credentials working!")
    print(f"Account: {identity['Account']}")
    print(f"User/Role: {identity['Arn']}")
except NoCredentialsError:
    print("No AWS credentials found. Configure via `aws configure` or set AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY env vars.")
except ClientError as e:
    print(f"Credentials found but request failed: {e}")
