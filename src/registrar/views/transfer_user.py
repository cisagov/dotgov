import logging
from django.db import transaction
from django.db.models import ForeignKey, OneToOneField, ManyToManyField, ManyToOneRel, ManyToManyRel, OneToOneRel

from django.shortcuts import render, get_object_or_404, redirect
from django.views import View
from registrar.models.domain import Domain
from registrar.models.domain_request import DomainRequest
from registrar.models.user import User
from django.contrib.admin import site
from django.contrib import messages

from registrar.models.user_domain_role import UserDomainRole
from registrar.models.user_portfolio_permission import UserPortfolioPermission

from registrar.utility.db_helpers import ignore_unique_violation

logger = logging.getLogger(__name__)


class TransferUserView(View):
    """Transfer user methods that set up the transfer_user template and handle the forms on it."""

    def get(self, request, user_id):
        """current_user refers to the 'source' user where the button that redirects to this view was clicked.
        other_users exclude current_user and populate a dropdown, selected_user is the selection in the dropdown.

        This also querries the relevant domains and domain requests, and the admin context needed for the sidenav."""

        current_user = get_object_or_404(User, pk=user_id)
        other_users = User.objects.exclude(pk=user_id).order_by(
            "first_name", "last_name"
        )  # Exclude the current user from the dropdown

        # Get the default admin site context, needed for the sidenav
        admin_context = site.each_context(request)

        context = {
            "current_user": current_user,
            "other_users": other_users,
            "logged_in_user": request.user,
            **admin_context,  # Include the admin context
            "current_user_domains": self.get_domains(current_user),
            "current_user_domain_requests": self.get_domain_requests(current_user),
            "current_user_portfolios": self.get_portfolios(current_user),
        }

        selected_user_id = request.GET.get("selected_user")
        if selected_user_id:
            selected_user = get_object_or_404(User, pk=selected_user_id)
            context["selected_user"] = selected_user
            context["selected_user_domains"] = self.get_domains(selected_user)
            context["selected_user_domain_requests"] = self.get_domain_requests(selected_user)
            context["selected_user_portfolios"] = self.get_portfolios(selected_user)

        return render(request, "admin/transfer_user.html", context)

    def post(self, request, user_id):
        """This handles the transfer from selected_user to current_user then deletes selected_user."""
        current_user = get_object_or_404(User, pk=user_id)
        selected_user_id = request.POST.get("selected_user")
        selected_user = get_object_or_404(User, pk=selected_user_id)

        try:
            # Make this atomic so that we don't get any partial transfers
            with transaction.atomic():
                change_logs = []

                # Dynamically handle related fields
                self.transfer_related_fields_and_log(selected_user, current_user, change_logs)

                # Success message if any related objects were updated
                if change_logs:
                    success_message = f"Data transferred successfully for the following objects: {change_logs}"
                    messages.success(request, success_message)

                selected_user.delete()
                messages.success(request, f"Deleted {selected_user} {selected_user.username}")
        except Exception as e:
            messages.error(request, f"An error occurred during the transfer: {e}")
            logger.error(f"An error occurred during the transfer: {e}", exc_info=True)

        return redirect("admin:registrar_user_change", object_id=user_id)

    def transfer_related_fields_and_log(self, selected_user, current_user, change_logs):
        """
        Dynamically find all related fields to the User model and transfer them from selected_user to current_user.
        Handles ForeignKey, OneToOneField, ManyToManyField, and ManyToOneRel relationships.
        """
        user_model = User

        for related_field in user_model._meta.get_fields():
            if related_field.is_relation:
                # Field objects represent forward relationships
                if isinstance(related_field, OneToOneField):
                    self._handle_one_to_one(related_field, selected_user, current_user, change_logs)
                elif isinstance(related_field, ManyToManyField):
                    self._handle_many_to_many(related_field, selected_user, current_user, change_logs)
                elif isinstance(related_field, ForeignKey):
                    self._handle_foreign_key(related_field, selected_user, current_user, change_logs)
                # Relationship objects represent reverse relationships
                elif isinstance(related_field, ManyToOneRel):
                    # ManyToOneRel is a reverse ForeignKey
                    self._handle_foreign_key_reverse(related_field, selected_user, current_user, change_logs)
                elif isinstance(related_field, OneToOneRel):
                    self._handle_one_to_one_reverse(related_field, selected_user, current_user, change_logs)
                elif isinstance(related_field, ManyToManyRel):
                    self._handle_many_to_many_reverse(related_field, selected_user, current_user, change_logs)
                else:
                    logger.error(f"Unknown relationship type for field {related_field}")
                    raise ValueError(f"Unknown relationship type for field {related_field}")

    def _handle_foreign_key_reverse(self, related_field: ManyToOneRel, selected_user, current_user, change_logs):
        # Handle reverse ForeignKey relationships
        related_manager = getattr(selected_user, related_field.get_accessor_name(), None)
        if related_manager and related_manager.exists():
            for related_object in related_manager.all():
                with ignore_unique_violation():
                    setattr(related_object, related_field.field.name, current_user)
                    related_object.save()
                self.log_change(related_object, selected_user, current_user, related_field.field.name, change_logs)

    def _handle_foreign_key(self, related_field: ForeignKey, selected_user, current_user, change_logs):
        # Handle ForeignKey relationships
        related_object = getattr(selected_user, related_field.name, None)
        if related_object:
            setattr(current_user, related_field.name, related_object)
            current_user.save()
            self.log_change(related_object, selected_user, current_user, related_field.name, change_logs)

    def _handle_one_to_one(self, related_field: OneToOneField, selected_user, current_user, change_logs):
        # Handle OneToOne relationship
        related_object = getattr(selected_user, related_field.name, None)
        if related_object:
            with ignore_unique_violation():
                setattr(current_user, related_field.name, related_object)
                current_user.save()
            self.log_change(related_object, selected_user, current_user, related_field.name, change_logs)

    def _handle_many_to_many(self, related_field: ManyToManyField, selected_user, current_user, change_logs):
        # Handle ManyToMany relationship
        related_name = related_field.remote_field.name
        related_manager = getattr(selected_user, related_name, None)
        if related_manager and related_manager.exists():
            for instance in related_manager.all():
                with ignore_unique_violation():
                    getattr(instance, related_name).remove(selected_user)
                    getattr(instance, related_name).add(current_user)
                self.log_change(instance, selected_user, current_user, related_name, change_logs)

    def _handle_many_to_many_reverse(self, related_field: ManyToManyRel, selected_user, current_user, change_logs):
        # Handle reverse relationship
        related_name = related_field.field.name
        related_manager = getattr(selected_user, related_name, None)
        if related_manager and related_manager.exists():
            for instance in related_manager.all():
                with ignore_unique_violation():
                    getattr(instance, related_name).remove(selected_user)
                    getattr(instance, related_name).add(current_user)
                self.log_change(instance, selected_user, current_user, related_name, change_logs)

    def _handle_one_to_one_reverse(self, related_field: OneToOneRel, selected_user, current_user, change_logs):
        # Handle reverse relationship
        field_name = related_field.get_accessor_name()
        related_instance = getattr(selected_user, field_name, None)
        if related_instance:
            setattr(related_instance, field_name, current_user)
            related_instance.save()
            self.log_change(related_instance, selected_user, current_user, field_name, change_logs)

    @classmethod
    def log_change(cls, obj, selected_user, current_user, field_name, change_logs):
        log_entry = f"Changed {field_name} from {selected_user} to {current_user} on {obj}"
        logger.info(log_entry)
        change_logs.append(log_entry)

    @classmethod
    def get_domains(cls, user):
        """A simplified version of domains_json"""
        user_domain_roles = UserDomainRole.objects.filter(user=user)
        domain_ids = user_domain_roles.values_list("domain_id", flat=True)
        domains = Domain.objects.filter(id__in=domain_ids)

        return domains

    @classmethod
    def get_domain_requests(cls, user):
        """A simplified version of domain_requests_json"""
        domain_requests = DomainRequest.objects.filter(creator=user)

        return domain_requests

    @classmethod
    def get_portfolios(cls, user):
        """Get portfolios"""
        portfolios = UserPortfolioPermission.objects.filter(user=user)

        return portfolios
