"""
Views to present the frontend
"""
from django.shortcuts import render
from django.contrib.auth.decorators import login_required


@login_required
def main(request):
    """
    Show the frontend
    """
    return render(request, 'frontend/main.html')
