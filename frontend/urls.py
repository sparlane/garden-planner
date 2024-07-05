"""
Urls for the frontend

These are linked at /
"""
from django.urls import re_path
from . import views

urlpatterns = [
    re_path('^$', views.main, name='main'),
]
