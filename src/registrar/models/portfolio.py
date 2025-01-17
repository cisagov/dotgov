from django.db import models

from registrar.models.domain_request import DomainRequest
from registrar.models.federal_agency import FederalAgency
from registrar.models.user import User
from registrar.models.utility.portfolio_helper import UserPortfolioRoleChoices

from .utility.time_stamped_model import TimeStampedModel


class Portfolio(TimeStampedModel):
    """
    Portfolio is used for organizing domains/domain-requests into
    manageable groups.
    """

    # Addresses the UnorderedObjectListWarning
    class Meta:
        ordering = ["organization_name"]

    # use the short names in Django admin
    OrganizationChoices = DomainRequest.OrganizationChoices
    StateTerritoryChoices = DomainRequest.StateTerritoryChoices

    # Stores who created this model. If no creator is specified in DJA,
    # then the creator will default to the current request user"""
    creator = models.ForeignKey(
        "registrar.User",
        on_delete=models.PROTECT,
        verbose_name="Portfolio creator",
        related_name="created_portfolios",
        unique=False,
    )

    organization_name = models.CharField(
        null=True,
        blank=True,
    )

    organization_type = models.CharField(
        max_length=255,
        choices=OrganizationChoices.choices,
        null=True,
        blank=True,
    )

    notes = models.TextField(
        null=True,
        blank=True,
    )

    federal_agency = models.ForeignKey(
        "registrar.FederalAgency",
        on_delete=models.PROTECT,
        unique=False,
        default=FederalAgency.get_non_federal_agency,
    )

    senior_official = models.ForeignKey(
        "registrar.SeniorOfficial",
        on_delete=models.PROTECT,
        unique=False,
        null=True,
        blank=True,
        related_name="portfolios",
    )

    address_line1 = models.CharField(
        null=True,
        blank=True,
        verbose_name="address line 1",
    )

    address_line2 = models.CharField(
        null=True,
        blank=True,
        verbose_name="address line 2",
    )

    city = models.CharField(
        null=True,
        blank=True,
    )

    # (imports enums from domain_request.py)
    state_territory = models.CharField(
        max_length=2,
        choices=StateTerritoryChoices.choices,
        null=True,
        blank=True,
        verbose_name="state, territory, or military post",
    )

    zipcode = models.CharField(
        max_length=10,
        null=True,
        blank=True,
        verbose_name="zip code",
    )

    urbanization = models.CharField(
        null=True,
        blank=True,
        help_text="Required for Puerto Rico only",
        verbose_name="urbanization",
    )

    security_contact_email = models.EmailField(
        null=True,
        blank=True,
        verbose_name="security contact e-mail",
        max_length=320,
    )

    def __str__(self) -> str:
        return str(self.organization_name)

    def save(self, *args, **kwargs):
        """Save override for custom properties"""

        # The urbanization field is only intended for the state_territory puerto rico
        if self.state_territory != self.StateTerritoryChoices.PUERTO_RICO and self.urbanization:
            self.urbanization = None

        super().save(*args, **kwargs)

    @property
    def federal_type(self):
        """Returns the federal_type value on the underlying federal_agency field"""
        return self.get_federal_type(self.federal_agency)

    @classmethod
    def get_federal_type(cls, federal_agency):
        return federal_agency.federal_type if federal_agency else None

    @property
    def portfolio_admin_users(self):
        """Gets all users with the role organization_admin for this particular portfolio.
        Returns a queryset of User."""
        admin_ids = self.portfolio_users.filter(
            roles__overlap=[
                UserPortfolioRoleChoices.ORGANIZATION_ADMIN,
            ],
        ).values_list("user__id", flat=True)
        return User.objects.filter(id__in=admin_ids)

    def portfolio_users_with_permissions(self, permissions=[]):
        """Gets all users with specified additional permissions for this particular portfolio.
        Returns a queryset of User."""
        portfolio_users = self.portfolio_users
        if permissions:
            portfolio_users = portfolio_users.filter(additional_permissions__overlap=permissions)
        user_ids = portfolio_users.values_list("user__id", flat=True)
        return User.objects.filter(id__in=user_ids)

    # == Getters for domains == #
    def get_domains(self, order_by=None):
        """Returns all DomainInformations associated with this portfolio"""
        if not order_by:
            return self.information_portfolio.all()
        else:
            return self.information_portfolio.all().order_by(*order_by)

    def get_domain_requests(self, order_by=None):
        """Returns all DomainRequests associated with this portfolio"""
        if not order_by:
            return self.DomainRequest_portfolio.all()
        else:
            return self.DomainRequest_portfolio.all().order_by(*order_by)

    # == Getters for suborganization == #
    def get_suborganizations(self):
        """Returns all suborganizations associated with this portfolio"""
        return self.portfolio_suborganizations.all()
