import csv

from django.http import HttpResponse

from django.views import View

from .donations import DonationListView

from .expenses import ExpenseListView

from .allocations import FundAllocationListView

from .common import OperationsPermissionRequiredMixin

from .projects import ProjectListView


class FilteredCsvExportView(OperationsPermissionRequiredMixin, View):
    list_view_class = None
    filename = 'export.csv'
    headers = ()
    row_builder = None

    def get(self, request, *args, **kwargs):
        """
        PRE: el usuario tiene permiso de lectura y la configuración declara columnas seguras.
        POST: descarga CSV con encabezados legibles y el mismo queryset filtrado del listado.
        """
        list_view = self.list_view_class()
        list_view.request = request
        queryset = list_view.get_queryset()
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="{self.filename}"'
        writer = csv.writer(response)
        writer.writerow(self.headers)
        for item in queryset:
            writer.writerow(self.row_builder(item))
        return response


class ProjectCsvExportView(FilteredCsvExportView):
    permission_required = 'operations.view_project'
    list_view_class = ProjectListView
    filename = 'proyectos.csv'
    headers = ('Código', 'Nombre', 'Estado', 'Presupuesto USD', 'Inicio', 'Cierre', 'Ubicación')
    row_builder = staticmethod(lambda item: (
        item.code, item.name, item.get_status_display(), str(item.estimated_budget),
        item.start_date or '', item.end_date or '', item.location,
    ))


class DonationCsvExportView(FilteredCsvExportView):
    permission_required = 'operations.view_donation'
    list_view_class = DonationListView
    filename = 'donaciones.csv'
    headers = ('Código', 'Institución donante', 'Monto', 'Moneda', 'Estado', 'Compromiso', 'Recepción')
    row_builder = staticmethod(lambda item: (
        item.code, item.donor.name, str(item.amount), item.currency,
        item.get_status_display(), item.commitment_date or '', item.received_date or '',
    ))


class FundAllocationCsvExportView(FilteredCsvExportView):
    permission_required = 'operations.view_fundallocation'
    list_view_class = FundAllocationListView
    filename = 'asignaciones.csv'
    headers = ('Código', 'Donación', 'Proyecto', 'Monto USD', 'Estado', 'Ejecución', 'Fecha', 'Categoría')
    row_builder = staticmethod(lambda item: (
        item.code, item.donation.code, item.project.code, str(item.amount),
        item.get_status_display(), item.execution_progress_label, item.allocation_date,
        item.get_budget_category_display(),
    ))


class ExpenseCsvExportView(FilteredCsvExportView):
    permission_required = 'operations.view_expense'
    list_view_class = ExpenseListView
    filename = 'gastos.csv'
    headers = ('Código', 'Proyecto', 'Asignación', 'Motivo', 'Monto', 'Moneda', 'Estado', 'Fecha')
    row_builder = staticmethod(lambda item: (
        item.code, item.allocation.project.code, item.allocation.code, item.reason,
        str(item.amount), item.currency, item.get_status_display(), item.expense_date,
    ))
