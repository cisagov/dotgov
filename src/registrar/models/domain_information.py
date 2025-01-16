from __future__ import annotations
from django.db import transaction

from registrar.models.utility.domain_helper import DomainHelper
from registrar.models.utility.generic_helper import CreateOrUpdateOrganizationTypeHelper
from registrar.utility.constants import BranchChoices

from .domain_request import DomainRequest
from .utility.time_stamped_model import TimeStampedModel

import logging

from django.db import models


logger = logging.getLogger(__name__)


class DomainInformation(TimeStampedModel):
    """A registrant's domain information for that domain, exported from
    DomainRequest. We use these field from DomainRequest with few exceptions
    which are 'removed' via pop at the bottom of this file. Most of design for domain
    management's user information are based on domain_request, but we cannot change
    the domain request once approved, so copying them that way we can make changes
    after its approved. Most fields here are copied from DomainRequest."""

    class Meta:
        """Contains meta information about this class"""

        indexes = [
            models.Index(fields=["domain"]),
            models.Index(fields=["domain_request"]),
        ]

        verbose_name_plural = "Domain information"

    StateTerritoryChoices = DomainRequest.StateTerritoryChoices

    # use the short names in Django admin
    OrganizationChoices = DomainRequest.OrganizationChoices

    federal_agency = models.ForeignKey(
        "registrar.FederalAgency",
        on_delete=models.PROTECT,
        help_text="Associated federal agency",
        unique=False,
        blank=True,
        null=True,
    )

    # This is the domain request user who created this domain request.
    creator = models.ForeignKey(
        "registrar.User",
        on_delete=models.PROTECT,
        related_name="information_created",
        help_text="Person who submitted the domain request",
    )

    # portfolio
    portfolio = models.ForeignKey(
        "registrar.Portfolio",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="information_portfolio",
    )

    sub_organization = models.ForeignKey(
        "registrar.Suborganization",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="information_sub_organization",
        help_text="If blank, domain is associated with the overarching organization for this portfolio.",
        verbose_name="Suborganization",
    )

    domain_request = models.OneToOneField(
        "registrar.DomainRequest",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="DomainRequest_info",
        help_text="Request associated with this domain",
        unique=True,
    )

    # ##### data fields from the initial form #####
    generic_org_type = models.CharField(
        max_length=255,
        choices=OrganizationChoices.choices,
        null=True,
        blank=True,
        help_text="Type of organization",
    )

    # TODO - Ticket #1911: stub this data from DomainRequest
    is_election_board = models.BooleanField(
        null=True,
        blank=True,
        verbose_name="election office",
    )

    organization_type = models.CharField(
        max_length=255,
        choices=DomainRequest.OrgChoicesElectionOffice.choices,
        null=True,
        blank=True,
        help_text='"Election" appears after the org type if it\'s an election office.',
    )

    federally_recognized_tribe = models.BooleanField(
        null=True,
    )

    state_recognized_tribe = models.BooleanField(
        null=True,
    )

    tribe_name = models.CharField(
        null=True,
        blank=True,
    )

    federal_type = models.CharField(
        max_length=50,
        choices=BranchChoices.choices,
        null=True,
        blank=True,
    )

    is_election_board = models.BooleanField(
        null=True,
        blank=True,
        verbose_name="election office",
    )

    organization_name = models.CharField(
        null=True,
        blank=True,
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

    about_your_organization = models.TextField(
        null=True,
        blank=True,
    )

    senior_official = models.ForeignKey(
        "registrar.Contact",
        null=True,
        blank=True,
        related_name="information_senior_official",
        on_delete=models.PROTECT,
    )

    domain = models.OneToOneField(
        "registrar.Domain",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        # Access this information via Domain as "domain.domain_info"
        related_name="domain_info",
    )

    purpose = models.TextField(
        null=True,
        blank=True,
        help_text="Purpose of your domain",
    )

    other_contacts = models.ManyToManyField(
        "registrar.Contact",
        blank=True,
        related_name="contact_domain_requests_information",
        verbose_name="Other employees",
    )

    no_other_contacts_rationale = models.TextField(
        null=True,
        blank=True,
        help_text="Required if creator does not list other employees",
    )

    anything_else = models.TextField(
        null=True,
        blank=True,
        verbose_name="Additional details",
    )

    # This is a drop-in replacement for a has_anything_else_text() function.
    # In order to track if the user has clicked the yes/no field (while keeping a none default), we need
    # a tertiary state. We should not display this in /admin.
    has_anything_else_text = models.BooleanField(
        null=True,
        blank=True,
        help_text="Determines if the user has a anything_else or not",
    )

    cisa_representative_email = models.EmailField(
        null=True,
        blank=True,
        verbose_name="CISA regional representative email",
        max_length=320,
    )

    cisa_representative_first_name = models.CharField(
        null=True,
        blank=True,
        verbose_name="CISA regional representative first name",
        db_index=True,
    )

    cisa_representative_last_name = models.CharField(
        null=True,
        blank=True,
        verbose_name="CISA regional representative last name",
        db_index=True,
    )

    # This is a drop-in replacement for an has_cisa_representative() function.
    # In order to track if the user has clicked the yes/no field (while keeping a none default), we need
    # a tertiary state. We should not display this in /admin.
    has_cisa_representative = models.BooleanField(
        null=True,
        blank=True,
        help_text="Determines if the user has a representative email or not",
    )

    is_policy_acknowledged = models.BooleanField(
        null=True,
        blank=True,
        help_text="Acknowledged .gov acceptable use policy",
    )

    notes = models.TextField(
        null=True,
        blank=True,
    )

    def __str__(self):
        try:
            if self.domain and self.domain.name:
                return self.domain.name
            else:
                return f"domain info set up and created by {self.creator}"
        except Exception:
            return ""

    def sync_yes_no_form_fields(self):
        """Some yes/no forms use a db field to track whether it was checked or not.
        We handle that here for def save().
        """
        # Check if the firstname or lastname of cisa representative has any data.
        # Then set the has_cisa_representative flag accordingly (so that it isn't
        # "none", which indicates an incomplete form).
        # This ensures that if we have prefilled data, the form is prepopulated
        if self.cisa_representative_first_name is not None or self.cisa_representative_last_name is not None:
            self.has_cisa_representative = (
                self.cisa_representative_first_name != "" and self.cisa_representative_last_name != ""
            )

        # Check for blank data and update has_cisa_representative accordingly (if it isn't None)
        if self.has_cisa_representative is not None:
            self.has_cisa_representative = (
                self.cisa_representative_first_name != "" and self.cisa_representative_first_name is not None
            ) and (self.cisa_representative_last_name != "" and self.cisa_representative_last_name is not None)

        # Check if anything_else has any data.
        # Then set the has_anything_else_text flag accordingly (so that it isn't
        # "none", which indicates an incomplete form).
        # This ensures that if we have prefilled data, the form is prepopulated
        if self.anything_else is not None:
            self.has_anything_else_text = self.anything_else != ""

        # Check for blank data and update has_anything_else_text accordingly (if it isn't None)
        if self.has_anything_else_text is not None:
            self.has_anything_else_text = self.anything_else != "" and self.anything_else is not None

    def sync_organization_type(self):
        """
        Updates the organization_type (without saving) to match
        the is_election_board and generic_organization_type fields.
        """

        # Define mappings between generic org and election org.
        # These have to be defined here, as you'd get a cyclical import error
        # otherwise.

        # For any given organization type, return the "_ELECTION" enum equivalent.
        # For example: STATE_OR_TERRITORY => STATE_OR_TERRITORY_ELECTION
        generic_org_map = DomainRequest.OrgChoicesElectionOffice.get_org_generic_to_org_election()

        # For any given "_election" variant, return the base org type.
        # For example: STATE_OR_TERRITORY_ELECTION => STATE_OR_TERRITORY
        election_org_map = DomainRequest.OrgChoicesElectionOffice.get_org_election_to_org_generic()

        # Manages the "organization_type" variable and keeps in sync with
        # "is_election_office" and "generic_organization_type"
        org_type_helper = CreateOrUpdateOrganizationTypeHelper(
            sender=self.__class__,
            instance=self,
            generic_org_to_org_map=generic_org_map,
            election_org_to_generic_org_map=election_org_map,
        )

        # Actually updates the organization_type field
        org_type_helper.create_or_update_organization_type()

        return self

    def save(self, *args, **kwargs):
        """Save override for custom properties"""
        self.sync_yes_no_form_fields()
        self.sync_organization_type()
        super().save(*args, **kwargs)

    @classmethod
    def create_from_da(cls, domain_request: DomainRequest, domain=None):
        """Takes in a DomainRequest and converts it into DomainInformation"""

        # Throw an error if we get None - we can't create something from nothing
        if domain_request is None:
            raise ValueError("The provided DomainRequest is None")

        # Throw an error if the da doesn't have an id
        if not hasattr(domain_request, "id"):
            raise ValueError("The provided DomainRequest has no id")

        # check if we have a record that corresponds with the domain
        # domain_request, if so short circuit the create
        existing_domain_info = cls.objects.filter(domain_request__id=domain_request.id).first()
        if existing_domain_info:
            logger.info(
                f"create_from_da() -> Shortcircuting create on {existing_domain_info}. "
                "This record already exists. No values updated!"
            )
            return existing_domain_info

        # Get the fields that exist on both DomainRequest and DomainInformation
        common_fields = DomainHelper.get_common_fields(DomainRequest, DomainInformation)

        # Get a list of all many_to_many relations on DomainInformation (needs to be saved differently)
        info_many_to_many_fields = DomainInformation._get_many_to_many_fields()

        # Create a dictionary with only the common fields, and create a DomainInformation from it
        da_dict = {}
        da_many_to_many_dict = {}
        for field in common_fields:
            # If the field isn't many_to_many, populate the da_dict.
            # If it is, populate da_many_to_many_dict as we need to save this later.
            if hasattr(domain_request, field):
                if field not in info_many_to_many_fields:
                    da_dict[field] = getattr(domain_request, field)
                else:
                    da_many_to_many_dict[field] = getattr(domain_request, field).all()

        # This will not happen in normal code flow, but having some redundancy doesn't hurt.
        # da_dict should not have "id" under any circumstances.
        # If it does have it, then this indicates that common_fields is overzealous in the data
        # that it is returning. Try looking in DomainHelper.get_common_fields.
        if "id" in da_dict:
            logger.warning("create_from_da() -> Found attribute 'id' when trying to create")
            da_dict.pop("id", None)

        # Create a placeholder DomainInformation object
        domain_info = DomainInformation(**da_dict)

        # Add the domain_request and domain fields
        domain_info.domain_request = domain_request
        if domain:
            domain_info.domain = domain

        # Save the instance and set the many-to-many fields.
        # Lumped under .atomic to ensure we don't make redundant DB calls.
        # This bundles them all together, and then saves it in a single call.
        with transaction.atomic():
            domain_info.save()
            for field, value in da_many_to_many_dict.items():
                getattr(domain_info, field).set(value)

        return domain_info

    @staticmethod
    def _get_many_to_many_fields():
        """Returns a set of each field.name that has the many to many relation"""
        return {field.name for field in DomainInformation._meta.many_to_many}  # type: ignore

    def get_state_display_of_domain(self):
        """Returns the state display of the underlying domain record"""
        if self.domain:
            return self.domain.get_state_display()
        else:
            return None

    # ----- Portfolio Properties -----

    @property
    def converted_organization_name(self):
        if self.portfolio:
            return self.portfolio.organization_name
        return self.organization_name

    @property
    def converted_generic_org_type(self):
        if self.portfolio:
            return self.portfolio.organization_type
        return self.generic_org_type

    @property
    def converted_federal_agency(self):
        if self.portfolio:
            return self.portfolio.federal_agency
        return self.federal_agency

    @property
    def converted_federal_type(self):
        if self.portfolio:
            return self.portfolio.federal_type
        return self.federal_type

    @property
    def converted_senior_official(self):
        if self.portfolio:
            return self.portfolio.display_senior_official
        return self.display_senior_official

    @property
    def converted_address_line1(self):
        if self.portfolio:
            return self.portfolio.display_address_line1
        return self.display_address_line1

    @property
    def converted_address_line2(self):
        if self.portfolio:
            return self.portfolio.display_address_line2
        return self.display_address_line2

    @property
    def converted_city(self):
        if self.portfolio:
            return self.portfolio.city
        return self.city

    @property
    def converted_state_territory(self):
        if self.portfolio:
            return self.portfolio.get_state_territory_display()
        return self.get_state_territory_display()

    @property
    def converted_zipcode(self):
        if self.portfolio:
            return self.portfolio.display_zipcode
        return self.display_zipcode

    @property
    def converted_urbanization(self):
        if self.portfolio:
            return self.portfolio.display_urbanization
        return self.display_urbanization

    # ----- Portfolio Properties (display values)-----
    @property
    def converted_generic_org_type_display(self):
        if self.portfolio:
            return self.portfolio.get_organization_type_display()
        return self.get_generic_org_type_display()

    @property
    def converted_federal_type_display(self):
        if self.portfolio:
            return self.portfolio.federal_agency.get_federal_type_display()
        return self.get_federal_type_display()
