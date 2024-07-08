"""
Views to present the frontend
"""
from django.shortcuts import render


def main(request):
    """
    Show the frontend
    """
    return render(request, 'frontend/main.html')
