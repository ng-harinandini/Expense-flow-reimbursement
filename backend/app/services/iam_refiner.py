from typing import Dict, Any

DEFAULT_BASIC_USER_IAM_POLICY = """{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BasicServerlessAccess",
      "Effect": "Allow",
      "Action": [
        "s3:*",
        "dynamodb:*",
        "lambda:InvokeFunction",
        "states:*",
        "textract:*"
      ],
      "Resource": "*"
    }
  ]
}"""

DEFAULT_REFINED_IAM_POLICY_SPEC = """{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "TextractReceiptScanScoped",
      "Effect": "Allow",
      "Action": [
        "textract:AnalyzeDocument",
        "textract:DetectDocumentText"
      ],
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "aws:RequestedRegion": "us-east-1"
        }
      }
    },
    {
      "Sid": "S3ReceiptBucketAccessScoped",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::expenseflow-receipts-sandbox-prod",
        "arn:aws:s3:::expenseflow-receipts-sandbox-prod/*"
      ],
      "Condition": {
        "Bool": {
          "aws:SecureTransport": "true"
        }
      }
    },
    {
      "Sid": "DynamoDBExpenseTableScoped",
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:Query"
      ],
      "Resource": "arn:aws:dynamodb:*:*:table/ExpenseClaimsTable"
    },
    {
      "Sid": "KMSKmsKeyForDataEncryption",
      "Effect": "Allow",
      "Action": [
        "kms:Decrypt",
        "kms:GenerateDataKey"
      ],
      "Resource": "arn:aws:kms:*:*:key/expenseflow-sandbox-cmk"
    },
    {
      "Sid": "StepFunctionsWorkflowExecution",
      "Effect": "Allow",
      "Action": [
        "states:StartExecution",
        "states:DescribeExecution"
      ],
      "Resource": "arn:aws:states:*:*:stateMachine:ExpenseApprovalWorkflow"
    }
  ]
}"""

def generate_default_iam_refinement() -> Dict[str, Any]:
    return {
        "refinedPolicyJson": DEFAULT_REFINED_IAM_POLICY_SPEC,
        "securityScore": 94,
        "summaryOfChanges": [
            'Removed wildcard "Resource": "*" on S3 and DynamoDB resources.',
            'Replaced dangerous s3:* wildcard with scoped actions (s3:GetObject, s3:PutObject, s3:ListBucket).',
            'Enforced aws:SecureTransport TLS condition on all S3 bucket operations.',
            'Scoped DynamoDB actions strictly to the ExpenseClaimsTable ARN.',
            'Added mandatory KMS CMK envelope encryption permissions (kms:Decrypt, kms:GenerateDataKey).'
        ],
        "leastPrivilegeViolationsFixed": [
            'Fixed CRITICAL violation: Overly permissive s3:* wildcard on all AWS S3 buckets.',
            'Fixed CRITICAL violation: dynamodb:* administrator permissions allowed unauthorized deletion.',
            'Fixed HIGH violation: Unencrypted S3 bucket transport (TLS missing).',
            'Fixed HIGH violation: Overly broad states:* permission on Step Functions.'
        ],
        "recommendedAwsServices": [
            'AWS Step Functions (Express Workflow for AI Orchestration)',
            'AWS Amazon Textract / Gemini OCR',
            'AWS EventBridge (Expense Event Bus)',
            'AWS DynamoDB (Audit Trail & Claims Store)',
            'AWS KMS (Customer Managed Key Encryption)',
            'AWS GuardDuty (Serverless Anomaly Detection)'
        ],
        "terraformCode": """# Terraform Sandbox Blueprint for ExpenseFlow AWS Serverless

resource "aws_kms_key" "expense_key" {
  description             = "KMS CMK for Expense Receipts & DynamoDB Claims"
  deletion_window_in_days = 7
  enable_key_rotation     = true
}

resource "aws_s3_bucket" "receipts_bucket" {
  bucket = "expenseflow-receipts-sandbox-prod"
}

resource "aws_s3_bucket_server_side_encryption_configuration" "s3_kms" {
  bucket = aws_s3_bucket.receipts_bucket.id

  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.expense_key.arn
      sse_algorithm     = "aws:kms"
    }
  }
}

resource "aws_dynamodb_table" "expense_claims" {
  name         = "ExpenseClaimsTable"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "ClaimID"

  attribute {
    name = "ClaimID"
    type = "S"
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.expense_key.arn
  }
}
""",
        "samTemplateYaml": """Transform: AWS::Serverless-2016-10-31
Description: ExpenseFlow AI - Enterprise Serverless Architecture Blueprint

Resources:
  ExpenseStateMachine:
    Type: AWS::Serverless::StateMachine
    Properties:
      DefinitionUri: statemachine/expense_workflow.asl.json
      Policies:
        - DynamoDBCrudPolicy:
            TableName: !Ref ExpenseClaimsTable
        - StepFunctionsExecutionPolicy:
            StateMachineName: ExpenseApprovalWorkflow

  ExpenseClaimsTable:
    Type: AWS::Serverless::SimpleTable
    Properties:
      PrimaryKey:
        Name: ClaimID
        Type: String
"""
    }
