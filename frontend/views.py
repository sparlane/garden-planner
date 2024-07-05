from django.shortcuts import render

# Create your views here.


def main(request):
    """
    Show the frontend
    """
    return render(request, 'frontend/main.html')