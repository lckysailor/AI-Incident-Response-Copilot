"""Pytest configuration and environment fixtures."""
import os

os.environ["ANTHROPIC_API_KEY"] = "mock-key"
os.environ["DD_API_KEY"] = "mock-dd-api-key"
os.environ["DD_APP_KEY"] = "mock-dd-app-key"
os.environ["SLACK_WEBHOOK_URL"] = ""
os.environ["PAGERDUTY_ROUTING_KEY"] = ""
