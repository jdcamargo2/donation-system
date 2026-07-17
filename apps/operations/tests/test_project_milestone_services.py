from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from apps.operations.milestones import get_milestone_progress
from apps.operations.models import AuditLog, Project, ProjectMilestone
from apps.operations.services import (
    ProjectMilestoneError,
    complete_project_milestone,
    create_project_milestone,
    delete_project_milestone,
    move_project_milestone_down,
    move_project_milestone_up,
    reopen_project_milestone,
    update_project_milestone,
)
from apps.operations.tests.helpers import create_project


PROJECT_REACHED_100_SUMMARY = 'El proyecto alcanzó el 100 % de sus hitos.'
PROJECT_LEFT_100_SUMMARY = 'El proyecto dejó de estar al 100 % de sus hitos.'


class ProjectMilestoneServiceTests(TestCase):
    def setUp(self):
        self.project = create_project()
        self.actor = get_user_model().objects.create_user(username='milestone-service-actor')

    def create_milestone(self, *, position, title=None, completed=False, project=None):
        """
        PRE: position is free in project and completed requests may use the service actor.
        POST: directly creates one valid fixture without service audit noise.
        """
        values = {
            'project': project or self.project,
            'title': title or f'Hito {position}',
            'position': position,
            'is_completed': completed,
        }
        if completed:
            values.update(completed_at=timezone.now(), completed_by=self.actor)
        return ProjectMilestone.objects.create(**values)

    def project_crossing_audits(self, project=None):
        return AuditLog.objects.filter(
            model_name='Proyecto',
            entity_id=str((project or self.project).pk),
            action=AuditLog.Action.UPDATED,
        )

    def assert_positions_are_consecutive(self, project=None):
        positions = list(
            (project or self.project).milestones.order_by('position').values_list(
                'position', flat=True
            )
        )
        self.assertEqual(positions, list(range(1, len(positions) + 1)))

    def test_create_appends_pending_normalized_milestone_and_audits(self):
        self.create_milestone(position=1)

        milestone = create_project_milestone(
            project_id=self.project.pk,
            title='  Entrega verificable  ',
            description='Descripción',
            actor=self.actor,
        )

        self.assertEqual(milestone.position, 2)
        self.assertEqual(milestone.title, 'Entrega verificable')
        self.assertEqual(milestone.created_by, self.actor)
        self.assertFalse(milestone.is_completed)
        self.assertIsNone(milestone.completed_at)
        self.assertIsNone(milestone.completed_by)
        self.assert_positions_are_consecutive()
        self.assertTrue(
            AuditLog.objects.filter(
                entity_id=str(milestone.pk), action=AuditLog.Action.CREATED, user=self.actor
            ).exists()
        )

    def test_create_pending_after_full_completion_audits_100_percent_exit(self):
        self.create_milestone(position=1, completed=True)

        create_project_milestone(
            project_id=self.project.pk,
            title='Nuevo hito pendiente',
            actor=self.actor,
        )

        progress = get_milestone_progress(list(self.project.milestones.all()))
        self.assertFalse(progress.is_completed)
        self.assertEqual(
            list(self.project_crossing_audits().values_list('summary', flat=True)),
            [PROJECT_LEFT_100_SUMMARY],
        )

    def test_update_changes_only_descriptive_fields_and_audits(self):
        creator = get_user_model().objects.create_user(username='milestone-original-creator')
        milestone = self.create_milestone(position=1, completed=True)
        milestone.created_by = creator
        milestone.save(update_fields=('created_by', 'updated_at'))
        completed_at = milestone.completed_at
        completed_by_id = milestone.completed_by_id

        updated = update_project_milestone(
            milestone.pk,
            title='  Título corregido  ',
            description='Descripción corregida',
            actor=self.actor,
        )

        self.assertEqual(updated.title, 'Título corregido')
        self.assertEqual(updated.description, 'Descripción corregida')
        self.assertTrue(updated.is_completed)
        self.assertEqual(updated.completed_at, completed_at)
        self.assertEqual(updated.completed_by_id, completed_by_id)
        self.assertEqual(updated.created_by, creator)
        self.assertEqual(
            AuditLog.objects.filter(
                entity_id=str(milestone.pk), action=AuditLog.Action.UPDATED
            ).count(),
            1,
        )

    def test_update_no_op_does_not_create_audit(self):
        milestone = self.create_milestone(position=1, title='Sin cambios')

        update_project_milestone(
            milestone.pk,
            title='  Sin cambios  ',
            description='',
            actor=self.actor,
        )

        self.assertFalse(
            AuditLog.objects.filter(
                entity_id=str(milestone.pk), action=AuditLog.Action.UPDATED
            ).exists()
        )

    def test_complete_assigns_server_metadata_audits_once_and_crosses_100_percent(self):
        milestone = self.create_milestone(position=1)
        before = timezone.now()

        completed = complete_project_milestone(milestone.pk, actor=self.actor)
        after = timezone.now()
        repeated = complete_project_milestone(milestone.pk, actor=self.actor)

        self.assertTrue(completed.is_completed)
        self.assertLessEqual(before, completed.completed_at)
        self.assertLessEqual(completed.completed_at, after)
        self.assertEqual(completed.completed_by, self.actor)
        self.assertEqual(repeated.completed_at, completed.completed_at)
        self.assertEqual(
            AuditLog.objects.filter(
                entity_id=str(milestone.pk), action=AuditLog.Action.COMPLETED
            ).count(),
            1,
        )
        self.assertEqual(
            list(self.project_crossing_audits().values_list('summary', flat=True)),
            [PROJECT_REACHED_100_SUMMARY],
        )
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, Project.Status.PLANNED)

    def test_complete_requires_existing_authenticated_actor(self):
        milestone = self.create_milestone(position=1)
        deleted_actor = get_user_model().objects.create_user(username='deleted-milestone-actor')
        deleted_actor.delete()

        for invalid_actor in (None, AnonymousUser(), deleted_actor):
            with self.subTest(actor=type(invalid_actor).__name__):
                with self.assertRaises(ProjectMilestoneError):
                    complete_project_milestone(milestone.pk, actor=invalid_actor)

        milestone.refresh_from_db()
        self.assertFalse(milestone.is_completed)

    def test_reopen_clears_metadata_audits_once_and_exits_100_percent(self):
        milestone = self.create_milestone(position=1, completed=True)

        reopened = reopen_project_milestone(milestone.pk, actor=self.actor)
        repeated = reopen_project_milestone(milestone.pk, actor=self.actor)

        self.assertFalse(reopened.is_completed)
        self.assertIsNone(reopened.completed_at)
        self.assertIsNone(reopened.completed_by)
        self.assertFalse(repeated.is_completed)
        self.assertEqual(
            AuditLog.objects.filter(
                entity_id=str(milestone.pk), action=AuditLog.Action.REOPENED
            ).count(),
            1,
        )
        self.assertEqual(
            list(self.project_crossing_audits().values_list('summary', flat=True)),
            [PROJECT_LEFT_100_SUMMARY],
        )

    def test_percentage_change_without_100_percent_crossing_does_not_audit_project(self):
        completed_later = self.create_milestone(position=1)
        self.create_milestone(position=2)

        complete_project_milestone(completed_later.pk, actor=self.actor)
        delete_project_milestone(completed_later.pk, actor=self.actor)

        self.assertFalse(self.project_crossing_audits().exists())

    def test_delete_pending_compacts_positions_and_can_reach_100_percent(self):
        first = self.create_milestone(position=1, completed=True)
        pending = self.create_milestone(position=2, completed=False)
        third = self.create_milestone(position=3, completed=True)
        deleted_label = str(pending)
        deleted_id = pending.pk

        result = delete_project_milestone(pending.pk, actor=self.actor)

        self.assertIsNone(result)
        self.assertFalse(ProjectMilestone.objects.filter(pk=deleted_id).exists())
        first.refresh_from_db()
        third.refresh_from_db()
        self.assertEqual((first.position, third.position), (1, 2))
        self.assert_positions_are_consecutive()
        deletion_audit = AuditLog.objects.get(
            entity_id=str(deleted_id), action=AuditLog.Action.DELETED
        )
        self.assertEqual(deletion_audit.entity_label, deleted_label)
        self.assertEqual(
            list(self.project_crossing_audits().values_list('summary', flat=True)),
            [PROJECT_REACHED_100_SUMMARY],
        )

    def test_delete_completed_milestone_can_leave_100_percent_or_zero_milestones(self):
        first = self.create_milestone(position=1, completed=True)
        second = self.create_milestone(position=2, completed=True)

        delete_project_milestone(second.pk, actor=self.actor)

        self.assertTrue(get_milestone_progress(list(self.project.milestones.all())).is_completed)
        self.assertFalse(self.project_crossing_audits().exists())

        delete_project_milestone(first.pk, actor=self.actor)

        progress = get_milestone_progress(list(self.project.milestones.all()))
        self.assertEqual(progress.total, 0)
        self.assertFalse(progress.is_completed)
        self.assertEqual(
            list(self.project_crossing_audits().values_list('summary', flat=True)),
            [PROJECT_LEFT_100_SUMMARY],
        )

    def test_move_up_and_down_use_consecutive_positions_and_audit_real_changes(self):
        first = self.create_milestone(position=1, title='Primero')
        second = self.create_milestone(position=2, title='Segundo')
        third = self.create_milestone(position=3, title='Tercero')

        move_project_milestone_up(third.pk, actor=self.actor)
        self.assertEqual(
            list(self.project.milestones.order_by('position').values_list('pk', flat=True)),
            [first.pk, third.pk, second.pk],
        )
        move_project_milestone_down(third.pk, actor=self.actor)
        self.assertEqual(
            list(self.project.milestones.order_by('position').values_list('pk', flat=True)),
            [first.pk, second.pk, third.pk],
        )
        self.assert_positions_are_consecutive()
        self.assertEqual(
            AuditLog.objects.filter(
                entity_id=str(third.pk), action=AuditLog.Action.REORDERED
            ).count(),
            2,
        )

    def test_move_at_boundaries_is_no_op_without_audit(self):
        first = self.create_milestone(position=1)
        last = self.create_milestone(position=2)

        move_project_milestone_up(first.pk, actor=self.actor)
        move_project_milestone_down(last.pk, actor=self.actor)

        self.assert_positions_are_consecutive()
        self.assertFalse(
            AuditLog.objects.filter(action=AuditLog.Action.REORDERED).exists()
        )

    def test_every_mutation_rejects_closed_and_annulled_projects(self):
        for status in (Project.Status.CLOSED, Project.Status.ANNULLED):
            with self.subTest(status=status):
                project = create_project(code=f'PRJ-MILESTONE-{status.upper()}')
                project.status = status
                project.save(update_fields=('status', 'updated_at'))
                pending = self.create_milestone(position=1, project=project)
                completed = self.create_milestone(position=2, completed=True, project=project)
                operations = (
                    lambda: create_project_milestone(
                        project_id=project.pk, title='Nuevo', actor=self.actor
                    ),
                    lambda: update_project_milestone(
                        pending.pk, title='Editado', actor=self.actor
                    ),
                    lambda: complete_project_milestone(pending.pk, actor=self.actor),
                    lambda: reopen_project_milestone(completed.pk, actor=self.actor),
                    lambda: delete_project_milestone(pending.pk, actor=self.actor),
                    lambda: move_project_milestone_down(pending.pk, actor=self.actor),
                    lambda: move_project_milestone_up(completed.pk, actor=self.actor),
                )
                for operation in operations:
                    with self.assertRaises(ProjectMilestoneError):
                        operation()

    def test_historical_null_completer_survives_later_service_edit(self):
        completer = get_user_model().objects.create_user(username='historical-completer')
        milestone = ProjectMilestone.objects.create(
            project=self.project,
            title='Hito histórico',
            position=1,
            is_completed=True,
            completed_at=timezone.now(),
            completed_by=completer,
        )
        completed_at = milestone.completed_at
        completer.delete()
        milestone.refresh_from_db()
        milestone.full_clean()

        updated = update_project_milestone(
            milestone.pk,
            title='Hito histórico editado',
            description='Edición posterior no relacionada',
            actor=self.actor,
        )

        self.assertTrue(updated.is_completed)
        self.assertEqual(updated.completed_at, completed_at)
        self.assertIsNone(updated.completed_by)

    def test_failed_outer_transaction_rolls_back_milestone_and_audit(self):
        with self.assertRaises(ValidationError):
            with transaction.atomic():
                milestone = create_project_milestone(
                    project_id=self.project.pk,
                    title='Debe revertirse',
                    actor=self.actor,
                )
                raise ValidationError('Fallo posterior controlado')

        self.assertFalse(ProjectMilestone.objects.filter(title='Debe revertirse').exists())
        self.assertFalse(
            AuditLog.objects.filter(
                model_name='Hito de proyecto', entity_id=str(milestone.pk)
            ).exists()
        )
