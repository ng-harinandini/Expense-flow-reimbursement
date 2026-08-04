"""Shared AWS Cognito IDP client factory (credentials via the standard boto3 chain)."""

from __future__ import annotations

import boto3

from app.core.config import settings


def cognito_client():
    """boto3 cognito-idp client in the resolved Cognito region."""
    return boto3.client("cognito-idp", region_name=settings.cognito_region)
