"""
Shared utilities for the garden-tracker project
"""
import json


def get_request_data(request):
    """
    Helper function to get data from request body or POST.
    Always returns a plain dict so callers don't depend on QueryDict-specific behaviour.
    """
    try:
        return json.loads(request.body)
    except json.JSONDecodeError:
        return request.POST.dict()
