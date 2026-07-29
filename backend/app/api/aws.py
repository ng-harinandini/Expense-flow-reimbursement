from fastapi import APIRouter, Depends
from app.services.iam_refiner import DEFAULT_REFINED_IAM_POLICY_SPEC
from app.core.deps import require_roles

router = APIRouter(prefix="/aws", tags=["AWS Architecture Export"])

@router.get("/export-code", dependencies=[Depends(require_roles("admin"))])
def export_aws_code():
    python_handler = """# ExpenseFlow AWS Lambda - Policy Engine & OCR Handler
import json
import os
import boto3

textract = boto3.client('textract')
dynamodb = boto3.resource('dynamodb')

def lambda_handler(event, context):
    \"\"\"
    AWS Step Functions Task 1: Analyze Receipt & Validate Expense Policy
    \"\"\"
    claim = event.get('claim', {})
    category = claim.get('category', 'Misc')
    amount = claim.get('amountUSD', 0.0)
    
    # 1. OCR Textract Extraction if receipt S3 object present
    s3_key = claim.get('receiptS3Key')
    if s3_key:
        response = textract.detect_document_text(
            Document={'S3Object': {'Bucket': os.environ['RECEIPTS_BUCKET'], 'Name': s3_key}}
        )
        print("Textract OCR response received:", len(response.get('Blocks', [])))
        
    # 2. Policy Engine Verification
    is_auto_approve = False
    if category == 'Meals' and amount <= 25.00:
        is_auto_approve = True
    elif category == 'Ground Transport' and amount <= 50.00:
        is_auto_approve = True
        
    return {
        'statusCode': 200,
        'claimId': claim.get('id'),
        'policyPassed': True,
        'autoApproved': is_auto_approve,
        'route': 'AUTO_APPROVE' if is_auto_approve else 'MANAGER_REVIEW'
    }
"""

    step_functions_json = {
        "Comment": "ExpenseFlow AI Approval State Machine",
        "StartAt": "OCRAndPolicyCheck",
        "States": {
            "OCRAndPolicyCheck": {
                "Type": "Task",
                "Resource": "arn:aws:lambda:us-east-1:123456789012:function:ExpensePolicyLambda",
                "Next": "ChoiceAutoApprove"
            },
            "ChoiceAutoApprove": {
                "Type": "Choice",
                "Choices": [
                    {
                        "Variable": "$.autoApproved",
                        "BooleanEquals": True,
                        "Next": "AutoApproveDisburse"
                    }
                ],
                "Default": "RouteToManagerReview"
            },
            "AutoApproveDisburse": {
                "Type": "Pass",
                "Result": "Expense Auto-Approved and queued for payout",
                "End": True
            },
            "RouteToManagerReview": {
                "Type": "Pass",
                "Result": "Claim routed to Manager Approval Queue",
                "End": True
            }
        }
    }

    return {
        "pythonHandler": python_handler,
        "stepFunctionsJson": step_functions_json,
        "iamPolicyJson": DEFAULT_REFINED_IAM_POLICY_SPEC
    }
