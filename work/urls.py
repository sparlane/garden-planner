"""REST routes for the nursery work queue."""

from rest_framework import routers

from .rest import AssigneeViewSet, WorkRuleViewSet, WorkTaskViewSet


router = routers.DefaultRouter()
router.register('rules', WorkRuleViewSet)
router.register('tasks', WorkTaskViewSet)
router.register('assignees', AssigneeViewSet, basename='work-assignee')

urlpatterns = router.urls
