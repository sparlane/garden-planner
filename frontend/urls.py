"""
Urls for the frontend

These are linked at /
"""
from django.urls import path
from . import views

urlpatterns = [
    path('', views.main, name='main'),
]
