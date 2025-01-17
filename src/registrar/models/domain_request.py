from __future__ import annotations
from typing import Union
import logging
from django.apps import apps
from django.conf import settings
from django.db import models
from django_fsm import FSMField, transition  # type: ignore
from django.utils import timezone
from registrar.models.domain import Domain
from registrar.models.federal_agency import FederalAgency
from registrar.models.utility.generic_helper import CreateOrUpdateOrganizationTypeHelper
from registrar.models.utility.portfolio_helper import UserPortfolioPermissionChoices
from registrar.utility.errors import FSMDomainRequestError, FSMErrorCodes
from registrar.utility.constants import BranchChoices
from auditlog.models import LogEntry
from django.core.exceptions import ValidationError

from registrar.utility.waffle import flag_is_active_for_user

from .utility.time_stamped_model import TimeStampedModel
from ..utility.email import send_templated_email, EmailSendingError
from itertools import chain

logger = logging.getLogger(__name__)


class DomainRequest(TimeStampedModel):
    """A registrant's domain request for a new domain."""

    class Meta:
        """Contains meta information about this class"""

        indexes = [
            models.Index(fields=["requested_domain"]),
            models.Index(fields=["approved_domain"]),
            models.Index(fields=["status"]),
        ]

    # https://django-auditlog.readthedocs.io/en/latest/usage.html#object-history
    # history = AuditlogHistoryField()

    # Constants for choice fields
    class DomainRequestStatus(models.TextChoices):
        IN_REVIEW = "in review", "In review"
        ACTION_NEEDED = "action needed", "Action needed"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        INELIGIBLE = "ineligible", "Ineligible"
        SUBMITTED = "submitted", "Submitted"
        WITHDRAWN = "withdrawn", "Withdrawn"
        STARTED = "started", "Started"

        @classmethod
        def get_status_label(cls, status_name: str):
            """Returns the associated label for a given status name"""
            return cls(status_name).label if status_name else None

    class StateTerritoryChoices(models.TextChoices):
        ALABAMA = "AL", "Alabama (AL)"
        ALASKA = "AK", "Alaska (AK)"
        AMERICAN_SAMOA = "AS", "American Samoa (AS)"
        ARIZONA = "AZ", "Arizona (AZ)"
        ARKANSAS = "AR", "Arkansas (AR)"
        CALIFORNIA = "CA", "California (CA)"
        COLORADO = "CO", "Colorado (CO)"
        CONNECTICUT = "CT", "Connecticut (CT)"
        DELAWARE = "DE", "Delaware (DE)"
        DISTRICT_OF_COLUMBIA = "DC", "District of Columbia (DC)"
        FLORIDA = "FL", "Florida (FL)"
        GEORGIA = "GA", "Georgia (GA)"
        GUAM = "GU", "Guam (GU)"
        HAWAII = "HI", "Hawaii (HI)"
        IDAHO = "ID", "Idaho (ID)"
        ILLINOIS = "IL", "Illinois (IL)"
        INDIANA = "IN", "Indiana (IN)"
        IOWA = "IA", "Iowa (IA)"
        KANSAS = "KS", "Kansas (KS)"
        KENTUCKY = "KY", "Kentucky (KY)"
        LOUISIANA = "LA", "Louisiana (LA)"
        MAINE = "ME", "Maine (ME)"
        MARYLAND = "MD", "Maryland (MD)"
        MASSACHUSETTS = "MA", "Massachusetts (MA)"
        MICHIGAN = "MI", "Michigan (MI)"
        MINNESOTA = "MN", "Minnesota (MN)"
        MISSISSIPPI = "MS", "Mississippi (MS)"
        MISSOURI = "MO", "Missouri (MO)"
        MONTANA = "MT", "Montana (MT)"
        NEBRASKA = "NE", "Nebraska (NE)"
        NEVADA = "NV", "Nevada (NV)"
        NEW_HAMPSHIRE = "NH", "New Hampshire (NH)"
        NEW_JERSEY = "NJ", "New Jersey (NJ)"
        NEW_MEXICO = "NM", "New Mexico (NM)"
        NEW_YORK = "NY", "New York (NY)"
        NORTH_CAROLINA = "NC", "North Carolina (NC)"
        NORTH_DAKOTA = "ND", "North Dakota (ND)"
        NORTHERN_MARIANA_ISLANDS = "MP", "Northern Mariana Islands (MP)"
        OHIO = "OH", "Ohio (OH)"
        OKLAHOMA = "OK", "Oklahoma (OK)"
        OREGON = "OR", "Oregon (OR)"
        PENNSYLVANIA = "PA", "Pennsylvania (PA)"
        PUERTO_RICO = "PR", "Puerto Rico (PR)"
        RHODE_ISLAND = "RI", "Rhode Island (RI)"
        SOUTH_CAROLINA = "SC", "South Carolina (SC)"
        SOUTH_DAKOTA = "SD", "South Dakota (SD)"
        TENNESSEE = "TN", "Tennessee (TN)"
        TEXAS = "TX", "Texas (TX)"
        UNITED_STATES_MINOR_OUTLYING_ISLANDS = (
            "UM",
            "United States Minor Outlying Islands (UM)",
        )
        UTAH = "UT", "Utah (UT)"
        VERMONT = "VT", "Vermont (VT)"
        VIRGIN_ISLANDS = "VI", "Virgin Islands (VI)"
        VIRGINIA = "VA", "Virginia (VA)"
        WASHINGTON = "WA", "Washington (WA)"
        WEST_VIRGINIA = "WV", "West Virginia (WV)"
        WISCONSIN = "WI", "Wisconsin (WI)"
        WYOMING = "WY", "Wyoming (WY)"
        ARMED_FORCES_AA = "AA", "Armed Forces Americas (AA)"
        ARMED_FORCES_AE = "AE", "Armed Forces Africa, Canada, Europe, Middle East (AE)"
        ARMED_FORCES_AP = "AP", "Armed Forces Pacific (AP)"

    class OrganizationChoices(models.TextChoices):
        """
        Primary organization choices:
        For use in the domain request experience
        Keys need to match OrgChoicesElectionOffice and OrganizationChoicesVerbose
        """

        FEDERAL = "federal", "Federal"
        INTERSTATE = "interstate", "Interstate"
        STATE_OR_TERRITORY = "state_or_territory", "State or territory"
        TRIBAL = "tribal", "Tribal"
        COUNTY = "county", "County"
        CITY = "city", "City"
        SPECIAL_DISTRICT = "special_district", "Special district"
        SCHOOL_DISTRICT = "school_district", "School district"

        @classmethod
        def get_org_label(cls, org_name: str):
            """Returns the associated label for a given org name"""
            # This is an edgecase on domains with no org.
            # This unlikely to happen but
            # a break will occur in certain edge cases without this.
            # (more specifically, csv exports).
            if not org_name:
                return None

            org_names = org_name.split("_election")
            if len(org_names) > 0:
                org_name = org_names[0]
            return cls(org_name).label if org_name else None

    class OrgChoicesElectionOffice(models.TextChoices):
        """
        Primary organization choices for Django admin:
        Keys need to match OrganizationChoices and OrganizationChoicesVerbose.

        The enums here come in two variants:
        Regular (matches the choices from OrganizationChoices)
        Election (Appends " - Election" to the string)

        When adding the election variant, you must append "_election" to the end of the string.
        """

        # We can't inherit OrganizationChoices due to models.TextChoices being an enum.
        # We can redefine these values instead.
        FEDERAL = "federal", "Federal"
        INTERSTATE = "interstate", "Interstate"
        STATE_OR_TERRITORY = "state_or_territory", "State or territory"
        TRIBAL = "tribal", "Tribal"
        COUNTY = "county", "County"
        CITY = "city", "City"
        SPECIAL_DISTRICT = "special_district", "Special district"
        SCHOOL_DISTRICT = "school_district", "School district"

        # Election variants
        STATE_OR_TERRITORY_ELECTION = "state_or_territory_election", "State or territory - Election"
        TRIBAL_ELECTION = "tribal_election", "Tribal - Election"
        COUNTY_ELECTION = "county_election", "County - Election"
        CITY_ELECTION = "city_election", "City - Election"
        SPECIAL_DISTRICT_ELECTION = "special_district_election", "Special district - Election"

        @classmethod
        def get_org_election_to_org_generic(cls):
            """
            Creates and returns a dictionary mapping from election-specific organization
            choice enums to their corresponding general organization choice enums.

            If no such mapping exists, it is simple excluded from the map.
            """
            # This can be mapped automatically but its harder to read.
            # For clarity reasons, we manually define this.
            org_election_map = {
                cls.STATE_OR_TERRITORY_ELECTION: cls.STATE_OR_TERRITORY,
                cls.TRIBAL_ELECTION: cls.TRIBAL,
                cls.COUNTY_ELECTION: cls.COUNTY,
                cls.CITY_ELECTION: cls.CITY,
                cls.SPECIAL_DISTRICT_ELECTION: cls.SPECIAL_DISTRICT,
            }
            return org_election_map

        @classmethod
        def get_org_generic_to_org_election(cls):
            """
            Creates and returns a dictionary mapping from general organization
            choice enums to their corresponding election-specific organization enums.

            If no such mapping exists, it is simple excluded from the map.
            """
            # This can be mapped automatically but its harder to read.
            # For clarity reasons, we manually define this.
            org_election_map = {
                cls.STATE_OR_TERRITORY: cls.STATE_OR_TERRITORY_ELECTION,
                cls.TRIBAL: cls.TRIBAL_ELECTION,
                cls.COUNTY: cls.COUNTY_ELECTION,
                cls.CITY: cls.CITY_ELECTION,
                cls.SPECIAL_DISTRICT: cls.SPECIAL_DISTRICT_ELECTION,
            }
            return org_election_map

        @classmethod
        def get_org_label(cls, org_name: str):
            # Translating the key that is given to the direct readable value
            return cls(org_name).label if org_name else None

    class OrganizationChoicesVerbose(models.TextChoices):
        """
        Tertiary organization choices
        For use in the domain request form and on the templates
        Keys need to match OrganizationChoices
        """

        FEDERAL = (
            "federal",
            "Federal: an agency of the U.S. government’s legislative, executive, or judicial branches",
        )
        INTERSTATE = "interstate", "Interstate: an organization of two or more states"
        STATE_OR_TERRITORY = (
            "state_or_territory",
            "State or territory: one of the 50 U.S. states, the District of Columbia, "
            "American Samoa, Guam, Northern Mariana Islands, Puerto Rico, or the U.S. "
            "Virgin Islands",
        )
        TRIBAL = (
            "tribal",
            "Tribal: a tribal government recognized by the federal or a state government",
        )
        COUNTY = "county", "County: a county, parish, or borough"
        CITY = "city", "City: a city, town, township, village, etc."
        SPECIAL_DISTRICT = (
            "special_district",
            "Special district: an independent government that delivers specialized, essential services",
        )
        SCHOOL_DISTRICT = (
            "school_district",
            "School district: a school district that is not part of a local government",
        )

    class RejectionReasons(models.TextChoices):
        DOMAIN_PURPOSE = "domain_purpose", "Purpose requirements not met"
        REQUESTOR_NOT_ELIGIBLE = "requestor_not_eligible", "Requestor not eligible to make request"
        ORG_HAS_DOMAIN = (
            "org_has_domain",
            "Org already has a .gov domain",
        )
        CONTACTS_NOT_VERIFIED = (
            "contacts_not_verified",
            "Org contacts couldn't be verified",
        )
        ORG_NOT_ELIGIBLE = "org_not_eligible", "Org not eligible for a .gov domain"
        NAMING_REQUIREMENTS = "naming_requirements", "Naming requirements not met"
        OTHER = "other", "Other/Unspecified"

        @classmethod
        def get_rejection_reason_label(cls, rejection_reason: str):
            """Returns the associated label for a given rejection reason"""
            return cls(rejection_reason).label if rejection_reason else None

    class ActionNeededReasons(models.TextChoices):
        """Defines common action needed reasons for domain requests"""

        ELIGIBILITY_UNCLEAR = ("eligibility_unclear", "Unclear organization eligibility")
        QUESTIONABLE_SENIOR_OFFICIAL = ("questionable_senior_official", "Questionable senior official")
        ALREADY_HAS_A_DOMAIN = ("already_has_a_domain", "Already has a domain")
        BAD_NAME = ("bad_name", "Doesn’t meet naming requirements")
        OTHER = ("other", "Other (no auto-email sent)")

        @classmethod
        def get_action_needed_reason_label(cls, action_needed_reason: str):
            """Returns the associated label for a given action needed reason"""
            return cls(action_needed_reason).label if action_needed_reason else None

    # #### Internal fields about the domain request #####
    status = FSMField(
        choices=DomainRequestStatus.choices,  # possible states as an array of constants
        default=DomainRequestStatus.STARTED,  # sensible default
        protected=False,  # can change state directly, particularly in Django admin
    )

    rejection_reason = models.TextField(
        choices=RejectionReasons.choices,
        null=True,
        blank=True,
    )

    rejection_reason_email = models.TextField(
        null=True,
        blank=True,
    )

    action_needed_reason = models.TextField(
        choices=ActionNeededReasons.choices,
        null=True,
        blank=True,
    )

    action_needed_reason_email = models.TextField(
        null=True,
        blank=True,
    )

    federal_agency = models.ForeignKey(
        "registrar.FederalAgency",
        on_delete=models.PROTECT,
        help_text="Associated federal agency",
        unique=False,
        blank=True,
        null=True,
    )

    # portfolio
    portfolio = models.ForeignKey(
        "registrar.Portfolio",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="DomainRequest_portfolio",
    )

    sub_organization = models.ForeignKey(
        "registrar.Suborganization",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="request_sub_organization",
        help_text="If blank, request is associated with the overarching organization for this portfolio.",
        verbose_name="Suborganization",
    )

    requested_suborganization = models.CharField(
        null=True,
        blank=True,
    )

    suborganization_city = models.CharField(
        null=True,
        blank=True,
    )

    suborganization_state_territory = models.CharField(
        max_length=2,
        choices=StateTerritoryChoices.choices,
        null=True,
        blank=True,
        verbose_name="state, territory, or military post",
    )

    # This is the domain request user who created this domain request.
    creator = models.ForeignKey(
        "registrar.User",
        on_delete=models.PROTECT,
        related_name="domain_requests_created",
        help_text="Person who submitted the domain request. Will receive email updates.",
    )

    investigator = models.ForeignKey(
        "registrar.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="domain_requests_investigating",
    )

    # ##### data fields from the initial form #####
    generic_org_type = models.CharField(
        max_length=255,
        # use the short names in Django admin
        choices=OrganizationChoices.choices,
        null=True,
        blank=True,
    )

    is_election_board = models.BooleanField(
        null=True,
        blank=True,
        verbose_name="election office",
    )

    # TODO - Ticket #1911: stub this data from DomainRequest
    organization_type = models.CharField(
        max_length=255,
        choices=OrgChoicesElectionOffice.choices,
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

    organization_name = models.CharField(
        null=True,
        blank=True,
    )

    address_line1 = models.CharField(
        null=True,
        blank=True,
        verbose_name="Address line 1",
    )
    address_line2 = models.CharField(
        null=True,
        blank=True,
        verbose_name="Address line 2",
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
    )

    about_your_organization = models.TextField(
        null=True,
        blank=True,
    )

    senior_official = models.ForeignKey(
        "registrar.Contact",
        null=True,
        blank=True,
        related_name="senior_official",
        on_delete=models.PROTECT,
    )

    # "+" means no reverse relation to lookup domain requests from Website
    current_websites = models.ManyToManyField(
        "registrar.Website",
        blank=True,
        related_name="current+",
        verbose_name="Current websites",
    )

    approved_domain = models.OneToOneField(
        "Domain",
        null=True,
        blank=True,
        help_text="Domain associated with this request; will be blank until request is approved",
        related_name="domain_request_approved_domain",
        on_delete=models.SET_NULL,
    )

    requested_domain = models.OneToOneField(
        "DraftDomain",
        null=True,
        blank=True,
        related_name="domain_request_requested_domain",
        on_delete=models.PROTECT,
    )

    alternative_domains = models.ManyToManyField(
        "registrar.Website",
        blank=True,
        related_name="alternatives+",
        help_text="Other domain names the creator provided for consideration",
    )

    purpose = models.TextField(
        null=True,
        blank=True,
    )

    other_contacts = models.ManyToManyField(
        "registrar.Contact",
        blank=True,
        related_name="contact_domain_requests",
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

    # Records when the domain request was first submitted
    first_submitted_date = models.DateField(
        null=True,
        blank=True,
        default=None,
        verbose_name="first submitted on",
        help_text="Date initially submitted",
    )

    # Records when domain request was last submitted
    last_submitted_date = models.DateField(
        null=True,
        blank=True,
        default=None,
        verbose_name="last submitted on",
        help_text="Date last submitted",
    )

    # Records when domain request status was last updated by an admin or analyst
    last_status_update = models.DateField(
        null=True,
        blank=True,
        default=None,
        verbose_name="last updated on",
        help_text="Date of the last status update",
    )

    notes = models.TextField(
        null=True,
        blank=True,
    )

    def is_awaiting_review(self) -> bool:
        """Checks if the current status is in submitted or in_review"""
        return self.status in [self.DomainRequestStatus.SUBMITTED, self.DomainRequestStatus.IN_REVIEW]

    def get_first_status_set_date(self, status):
        """Returns the date when the domain request was first set to the given status."""
        log_entry = (
            LogEntry.objects.filter(content_type__model="domainrequest", object_pk=self.pk, changes__status__1=status)
            .order_by("-timestamp")
            .first()
        )
        return log_entry.timestamp.date() if log_entry else None

    def get_first_status_started_date(self):
        """Returns the date when the domain request was put into the status "started" for the first time"""
        return self.get_first_status_set_date(DomainRequest.DomainRequestStatus.STARTED)

    @classmethod
    def get_statuses_that_send_emails(cls):
        """Returns a list of statuses that send an email to the user"""
        excluded_statuses = [cls.DomainRequestStatus.INELIGIBLE, cls.DomainRequestStatus.IN_REVIEW]
        return [status for status in cls.DomainRequestStatus if status not in excluded_statuses]

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
        generic_org_map = self.OrgChoicesElectionOffice.get_org_generic_to_org_election()

        # For any given "_election" variant, return the base org type.
        # For example: STATE_OR_TERRITORY_ELECTION => STATE_OR_TERRITORY
        election_org_map = self.OrgChoicesElectionOffice.get_org_election_to_org_generic()

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

    def _cache_status_and_status_reasons(self):
        """Maintains a cache of properties so we can avoid a DB call"""
        self._cached_action_needed_reason = self.action_needed_reason
        self._cached_rejection_reason = self.rejection_reason
        self._cached_status = self.status

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Store original values for caching purposes. Used to compare them on save.
        self._cache_status_and_status_reasons()

    def clean(self):
        """
        Validates suborganization-related fields in two scenarios:
        1. New suborganization request: Prevents duplicate names within same portfolio
        2. Partial suborganization data: Enforces a all-or-nothing rule for city/state/name fields
        when portfolio exists without selected suborganization

        Add new domain request validation rules here to ensure they're
        enforced during both model save and form submission.
        Not presently used on the domain request wizard, though.
        """
        super().clean()
        # Validation logic for a suborganization request
        if self.is_requesting_new_suborganization():
            # Raise an error if this suborganization already exists
            Suborganization = apps.get_model("registrar.Suborganization")
            if (
                self.requested_suborganization
                and Suborganization.objects.filter(
                    name__iexact=self.requested_suborganization,
                    portfolio=self.portfolio,
                    name__isnull=False,
                    portfolio__isnull=False,
                ).exists()
            ):
                # Add a field-level error to requested_suborganization.
                # To pass in field-specific errors, we need to embed a dict of
                # field: validationerror then pass that into a validation error itself.
                # This is slightly confusing, but it just adds it at that level.
                msg = (
                    "This suborganization already exists. "
                    "Choose a new name, or select it directly if you would like to use it."
                )
                errors = {"requested_suborganization": ValidationError(msg)}
                raise ValidationError(errors)
        elif self.portfolio and not self.sub_organization:
            # You cannot create a new suborganization without these fields
            required_suborg_fields = {
                "requested_suborganization": self.requested_suborganization,
                "suborganization_city": self.suborganization_city,
                "suborganization_state_territory": self.suborganization_state_territory,
            }
            # If at least one value is populated, enforce a all-or-nothing rule
            if any(bool(value) for value in required_suborg_fields.values()):
                # Find which fields are empty and throw an error on the field
                errors = {}
                for field_name, value in required_suborg_fields.items():
                    if not value:
                        errors[field_name] = ValidationError(
                            "This field is required when creating a new suborganization.",
                        )
                raise ValidationError(errors)

    def save(self, *args, **kwargs):
        """Save override for custom properties"""
        self.sync_organization_type()
        self.sync_yes_no_form_fields()

        if self._cached_status != self.status:
            self.last_status_update = timezone.now().date()

        super().save(*args, **kwargs)

        # Handle custom status emails.
        # An email is sent out when a, for example, action_needed_reason is changed or added.
        statuses_that_send_custom_emails = [self.DomainRequestStatus.ACTION_NEEDED, self.DomainRequestStatus.REJECTED]
        if self.status in statuses_that_send_custom_emails:
            self.send_custom_status_update_email(self.status)

        # Update the cached values after saving
        self._cache_status_and_status_reasons()

    def create_requested_suborganization(self):
        """Creates the requested suborganization.
        Adds the name, portfolio, city, and state_territory fields.
        Returns the created suborganization."""
        Suborganization = apps.get_model("registrar.Suborganization")
        return Suborganization.objects.create(
            name=self.requested_suborganization,
            portfolio=self.portfolio,
            city=self.suborganization_city,
            state_territory=self.suborganization_state_territory,
        )

    def send_custom_status_update_email(self, status):
        """Helper function to send out a second status email when the status remains the same,
        but the reason has changed."""

        # Currently, we store all this information in three variables.
        # When adding new reasons, this can be a lot to manage so we store it here
        # in a centralized location. However, this may need to change if this scales.
        status_information = {
            self.DomainRequestStatus.ACTION_NEEDED: {
                "cached_reason": self._cached_action_needed_reason,
                "reason": self.action_needed_reason,
                "email": self.action_needed_reason_email,
                "excluded_reasons": [DomainRequest.ActionNeededReasons.OTHER],
                "wrap_email": True,
            },
            self.DomainRequestStatus.REJECTED: {
                "cached_reason": self._cached_rejection_reason,
                "reason": self.rejection_reason,
                "email": self.rejection_reason_email,
                "excluded_reasons": [],
                # "excluded_reasons": [DomainRequest.RejectionReasons.OTHER],
                "wrap_email": False,
            },
        }
        status_info = status_information.get(status)

        # Don't send an email if there is nothing to send.
        if status_info.get("email") is None:
            logger.warning("send_custom_status_update_email() => Tried sending an empty email.")
            return

        # We should never send an email if no reason was specified.
        # Additionally, Don't send out emails for reasons that shouldn't send them.
        if status_info.get("reason") is None or status_info.get("reason") in status_info.get("excluded_reasons"):
            logger.warning("send_custom_status_update_email() => Tried sending a status email without a reason.")
            return

        # Only send out an email if the underlying reason itself changed or if no email was sent previously.
        if status_info.get("cached_reason") != status_info.get("reason") or status_info.get("cached_reason") is None:
            bcc_address = settings.DEFAULT_FROM_EMAIL if settings.IS_PRODUCTION else ""
            self._send_status_update_email(
                new_status=status,
                email_template="emails/includes/custom_email.txt",
                email_template_subject="emails/status_change_subject.txt",
                bcc_address=bcc_address,
                custom_email_content=status_info.get("email"),
                wrap_email=status_information.get("wrap_email"),
            )

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

    def __str__(self):
        try:
            if self.requested_domain and self.requested_domain.name:
                return self.requested_domain.name
            else:
                return f"{self.status} domain request created by {self.creator}"
        except Exception:
            return ""

    def domain_is_not_active(self):
        if self.approved_domain:
            return not self.approved_domain.is_active()
        return True

    def delete_and_clean_up_domain(self, called_from):
        # Delete the approved domain
        try:
            # Clean up the approved domain
            domain_state = self.approved_domain.state
            # Only reject if it exists on EPP
            if domain_state != Domain.State.UNKNOWN:
                self.approved_domain.deletedInEpp()
                self.approved_domain.save()
            self.approved_domain.delete()
            self.approved_domain = None
        except Exception as err:
            logger.error(err)
            logger.error(f"Can't query an approved domain while attempting {called_from}")

        # Delete the suborg as long as this is the only place it is used
        self._cleanup_dangling_suborg()

    def _cleanup_dangling_suborg(self):
        """Deletes the existing suborg if its only being used by the deleted record"""
        # Nothing to delete, so we just smile and walk away
        if self.sub_organization is None:
            return

        Suborganization = apps.get_model("registrar.Suborganization")

        # Stored as so because we need to set the reference to none first,
        # so we can't just use the self.sub_organization property
        suborg = Suborganization.objects.get(id=self.sub_organization.id)
        requests = suborg.request_sub_organization
        domain_infos = suborg.information_sub_organization

        # Check if this is the only reference to the suborganization
        if requests.count() != 1 or domain_infos.count() > 1:
            return

        # Remove the suborganization reference from request.
        self.sub_organization = None
        self.save()

        # Remove the suborganization reference from domain if it exists.
        if domain_infos.count() == 1:
            domain_infos.update(sub_organization=None)

        # Delete the now-orphaned suborganization
        logger.info(f"_cleanup_dangling_suborg() -> Deleting orphan suborganization: {suborg}")
        suborg.delete()

    def _send_status_update_email(
        self,
        new_status,
        email_template,
        email_template_subject,
        bcc_address="",
        cc_addresses: list[str] = [],
        context=None,
        send_email=True,
        wrap_email=False,
        custom_email_content=None,
    ):
        """Send a status update email to the creator.

        The email goes to the email address that the creator gave as their
        contact information. If there is not creator information, then do
        nothing.

        Optional args:
        bcc_address: str -> the address to bcc to

        context: dict -> The context sent to the template

        send_email: bool -> Used to bypass the send_templated_email function, in the event
        we just want to log that an email would have been sent, rather than actually sending one.

        wrap_email: bool -> Wraps emails using `wrap_text_and_preserve_paragraphs` if any given
        paragraph exceeds our desired max length (for prettier display).

        custom_email_content: str -> Renders an email with the content of this string as its body text.
        """

        recipient = self.creator
        if recipient is None or recipient.email is None:
            logger.warning(
                f"Cannot send {new_status} email, no creator email address for domain request with pk: {self.pk}."
                f" Name: {self.requested_domain.name}"
                if self.requested_domain
                else ""
            )
            return None

        if not send_email:
            logger.info(f"Email was not sent. Would send {new_status} email to: {recipient.email}")
            return None

        try:
            if not context:
                has_organization_feature_flag = flag_is_active_for_user(recipient, "organization_feature")
                is_org_user = has_organization_feature_flag and recipient.has_base_portfolio_permission(self.portfolio)
                context = {
                    "domain_request": self,
                    # This is the user that we refer to in the email
                    "recipient": recipient,
                    "is_org_user": is_org_user,
                }

            if custom_email_content:
                context["custom_email_content"] = custom_email_content
            send_templated_email(
                email_template,
                email_template_subject,
                recipient.email,
                context=context,
                bcc_address=bcc_address,
                cc_addresses=cc_addresses,
                wrap_email=wrap_email,
            )
            logger.info(f"The {new_status} email sent to: {recipient.email}")
        except EmailSendingError:
            logger.warning("Failed to send confirmation email", exc_info=True)

    def investigator_exists_and_is_staff(self):
        """Checks if the current investigator is in a valid state for a state transition"""
        is_valid = True
        # Check if an investigator is assigned. No approval is possible without one.
        if self.investigator is None or not self.investigator.is_staff:
            is_valid = False
        return is_valid

    @transition(
        field="status",
        source=[
            DomainRequestStatus.STARTED,
            DomainRequestStatus.IN_REVIEW,
            DomainRequestStatus.ACTION_NEEDED,
            DomainRequestStatus.WITHDRAWN,
        ],
        target=DomainRequestStatus.SUBMITTED,
    )
    def submit(self):
        """Submit an domain request that is started.

        As a side effect, an email notification is sent."""

        # check our conditions here inside the `submit` method so that we
        # can raise more informative exceptions

        # requested_domain could be None here
        if not hasattr(self, "requested_domain") or self.requested_domain is None:
            raise ValueError("Requested domain is missing.")

        DraftDomain = apps.get_model("registrar.DraftDomain")
        if not DraftDomain.string_could_be_domain(self.requested_domain.name):
            raise ValueError("Requested domain is not a valid domain name.")
        # if the domain has not been submitted before this  must be the first time
        if not self.first_submitted_date:
            self.first_submitted_date = timezone.now().date()

        # Update last_submitted_date to today
        self.last_submitted_date = timezone.now().date()
        self.save()

        # Limit email notifications to transitions from Started and Withdrawn
        limited_statuses = [self.DomainRequestStatus.STARTED, self.DomainRequestStatus.WITHDRAWN]

        bcc_address = ""
        if settings.IS_PRODUCTION:
            bcc_address = settings.DEFAULT_FROM_EMAIL

        cc_addresses: list[str] = []
        if self.requesting_entity_is_portfolio():
            portfolio_view_requests_users = self.portfolio.portfolio_users_with_permissions(
                permissions=[UserPortfolioPermissionChoices.VIEW_ALL_REQUESTS]
            )
            cc_users = self.portfolio.portfolio_admin_users | portfolio_view_requests_users
            cc_addresses = list(cc_users.values_list("email", flat=True))

        if self.status in limited_statuses:
            self._send_status_update_email(
                "submission confirmation",
                "emails/submission_confirmation.txt",
                "emails/submission_confirmation_subject.txt",
                send_email=True,
                bcc_address=bcc_address,
                cc_addresses=cc_addresses,
            )

    @transition(
        field="status",
        source=[
            DomainRequestStatus.SUBMITTED,
            DomainRequestStatus.ACTION_NEEDED,
            DomainRequestStatus.APPROVED,
            DomainRequestStatus.REJECTED,
            DomainRequestStatus.INELIGIBLE,
        ],
        target=DomainRequestStatus.IN_REVIEW,
        conditions=[domain_is_not_active, investigator_exists_and_is_staff],
    )
    def in_review(self):
        """Investigate an domain request that has been submitted.

        This action is logged.

        This action cleans up the rejection status if moving away from rejected.

        As side effects this will delete the domain and domain_information
        (will cascade) when they exist."""

        if self.status == self.DomainRequestStatus.APPROVED:
            self.delete_and_clean_up_domain("in_review")
        elif self.status == self.DomainRequestStatus.REJECTED:
            self.rejection_reason = None
        elif self.status == self.DomainRequestStatus.ACTION_NEEDED:
            self.action_needed_reason = None

        literal = DomainRequest.DomainRequestStatus.IN_REVIEW
        # Check if the tuple exists, then grab its value
        in_review = literal if literal is not None else "In Review"
        logger.info(f"A status change occurred. {self} was changed to '{in_review}'")

    @transition(
        field="status",
        source=[
            DomainRequestStatus.IN_REVIEW,
            DomainRequestStatus.APPROVED,
            DomainRequestStatus.REJECTED,
            DomainRequestStatus.INELIGIBLE,
        ],
        target=DomainRequestStatus.ACTION_NEEDED,
        conditions=[domain_is_not_active, investigator_exists_and_is_staff],
    )
    def action_needed(self):
        """Send back an domain request that is under investigation or rejected.

        This action is logged.

        This action cleans up the rejection status if moving away from rejected.

        As side effects this will delete the domain and domain_information
        (will cascade) when they exist.

        Afterwards, we send out an email for action_needed in def save().
        See the function send_custom_status_update_email.
        """

        if self.status == self.DomainRequestStatus.APPROVED:
            self.delete_and_clean_up_domain("action_needed")

        elif self.status == self.DomainRequestStatus.REJECTED:
            self.rejection_reason = None

        # Check if the tuple is setup correctly, then grab its value.

        literal = DomainRequest.DomainRequestStatus.ACTION_NEEDED
        action_needed = literal if literal is not None else "Action Needed"
        logger.info(f"A status change occurred. {self} was changed to '{action_needed}'")

    @transition(
        field="status",
        source=[
            DomainRequestStatus.SUBMITTED,
            DomainRequestStatus.IN_REVIEW,
            DomainRequestStatus.ACTION_NEEDED,
            DomainRequestStatus.REJECTED,
        ],
        target=DomainRequestStatus.APPROVED,
        conditions=[investigator_exists_and_is_staff],
    )
    def approve(self, send_email=True):
        """Approve an domain request that has been submitted.

        This action cleans up the rejection status if moving away from rejected.

        This has substantial side-effects because it creates another database
        object for the approved Domain and makes the user who created the
        domain request into an admin on that domain. It also triggers an email
        notification."""

        should_save = False
        if self.federal_agency is None:
            self.federal_agency = FederalAgency.objects.filter(agency="Non-Federal Agency").first()
            should_save = True

        if self.is_requesting_new_suborganization():
            self.sub_organization = self.create_requested_suborganization()
            should_save = True

        if should_save:
            self.save()

        # create the domain
        Domain = apps.get_model("registrar.Domain")

        # == Check that the domain_request is valid == #
        if Domain.objects.filter(name=self.requested_domain.name).exists():
            raise FSMDomainRequestError(code=FSMErrorCodes.APPROVE_DOMAIN_IN_USE)

        # == Create the domain and related components == #
        created_domain = Domain.objects.create(name=self.requested_domain.name)
        self.approved_domain = created_domain

        # copy the information from DomainRequest into domaininformation
        DomainInformation = apps.get_model("registrar.DomainInformation")
        DomainInformation.create_from_da(domain_request=self, domain=created_domain)

        # create the permission for the user
        UserDomainRole = apps.get_model("registrar.UserDomainRole")
        UserDomainRole.objects.get_or_create(
            user=self.creator, domain=created_domain, role=UserDomainRole.Roles.MANAGER
        )

        if self.status == self.DomainRequestStatus.REJECTED:
            self.rejection_reason = None
        elif self.status == self.DomainRequestStatus.ACTION_NEEDED:
            self.action_needed_reason = None

        # == Send out an email == #
        self._send_status_update_email(
            "domain request approved",
            "emails/status_change_approved.txt",
            "emails/status_change_approved_subject.txt",
            send_email=send_email,
        )

    def is_withdrawable(self):
        """Helper function that determines if the request can be withdrawn in its current status"""
        # This list is equivalent to the source field on withdraw. We need a better way to
        # consolidate these two lists - i.e. some sort of method that keeps these two lists in sync.
        # django fsm is very picky with what we can define in that field.
        return self.status in [
            self.DomainRequestStatus.SUBMITTED,
            self.DomainRequestStatus.IN_REVIEW,
            self.DomainRequestStatus.ACTION_NEEDED,
        ]

    @transition(
        field="status",
        source=[DomainRequestStatus.SUBMITTED, DomainRequestStatus.IN_REVIEW, DomainRequestStatus.ACTION_NEEDED],
        target=DomainRequestStatus.WITHDRAWN,
    )
    def withdraw(self):
        """Withdraw an domain request that has been submitted."""

        self._send_status_update_email(
            "withdraw",
            "emails/domain_request_withdrawn.txt",
            "emails/domain_request_withdrawn_subject.txt",
        )

    @transition(
        field="status",
        source=[DomainRequestStatus.IN_REVIEW, DomainRequestStatus.ACTION_NEEDED, DomainRequestStatus.APPROVED],
        target=DomainRequestStatus.REJECTED,
        conditions=[domain_is_not_active, investigator_exists_and_is_staff],
    )
    def reject(self):
        """Reject an domain request that has been submitted.

        This action is logged.

        This action cleans up the action needed status if moving away from action needed.

        As side effects this will delete the domain and domain_information
        (will cascade) when they exist.

        Afterwards, we send out an email for reject in def save().
        See the function send_custom_status_update_email.
        """

        if self.status == self.DomainRequestStatus.APPROVED:
            self.delete_and_clean_up_domain("reject")

    @transition(
        field="status",
        source=[
            DomainRequestStatus.IN_REVIEW,
            DomainRequestStatus.ACTION_NEEDED,
            DomainRequestStatus.APPROVED,
            DomainRequestStatus.REJECTED,
        ],
        target=DomainRequestStatus.INELIGIBLE,
        conditions=[domain_is_not_active, investigator_exists_and_is_staff],
    )
    def reject_with_prejudice(self):
        """The applicant is a bad actor, reject with prejudice.

        No email As a side effect, but we block the applicant from editing
        any existing domains/domain requests and from submitting new aplications.
        We do this by setting an ineligible status on the user, which the
        permissions classes test against. This will also delete the domain
        and domain_information (will cascade) when they exist."""

        if self.status == self.DomainRequestStatus.APPROVED:
            self.delete_and_clean_up_domain("reject_with_prejudice")

        self.creator.restrict_user()

    def requesting_entity_is_portfolio(self) -> bool:
        """Determines if this record is requesting that a portfolio be their organization.
        Used for the RequestingEntity page.
        Returns True if the portfolio exists and if organization_name matches portfolio.organization_name.
        """
        if self.portfolio and self.organization_name == self.portfolio.organization_name:
            return True
        return False

    def requesting_entity_is_suborganization(self) -> bool:
        """Determines if this record is also requesting that it be tied to a suborganization.
        Used for the RequestingEntity page.
        Returns True if portfolio exists and either sub_organization exists,
        or if is_requesting_new_suborganization() is true.
        Returns False otherwise.
        """
        if self.portfolio and (self.sub_organization or self.is_requesting_new_suborganization()):
            return True
        return False

    def is_requesting_new_suborganization(self) -> bool:
        """Determines if a user is trying to request
        a new suborganization using the domain request form, rather than one that already exists.
        Used for the RequestingEntity page and on DomainInformation.create_from_da().

        Returns True if a sub_organization does not exist and if requested_suborganization,
        suborganization_city, and suborganization_state_territory all exist.
        Returns False otherwise.
        """

        # If a suborganization already exists, it can't possibly be a new one.
        # As well, we need all required fields to exist.
        required_fields = [
            self.requested_suborganization,
            self.suborganization_city,
            self.suborganization_state_territory,
        ]
        if not self.sub_organization and all(required_fields):
            return True
        return False

    # ## Form unlocking steps ## #
    #
    # These methods control the conditions in which we should unlock certain domain wizard steps.

    def unlock_requesting_entity(self) -> bool:
        """Unlocks the requesting entity step. Used for the RequestingEntity page.
        Returns true if requesting_entity_is_suborganization() and requesting_entity_is_portfolio().
        Returns False otherwise.
        """
        if self.requesting_entity_is_suborganization() or self.requesting_entity_is_portfolio():
            return True
        return False

    # ## Form policies ## #
    #
    # These methods control what questions need to be answered by applicants
    # during the domain request flow. They are policies about the domain request so
    # they appear here.

    def show_organization_federal(self) -> bool:
        """Show this step if the answer to the first question was "federal"."""
        user_choice = self.generic_org_type
        return user_choice == DomainRequest.OrganizationChoices.FEDERAL

    def show_tribal_government(self) -> bool:
        """Show this step if the answer to the first question was "tribal"."""
        user_choice = self.generic_org_type
        return user_choice == DomainRequest.OrganizationChoices.TRIBAL

    def show_organization_election(self) -> bool:
        """Show this step if the answer to the first question implies it.

        This shows for answers that aren't "Federal" or "Interstate".
        This also doesnt show if user selected "School District" as well (#524)
        """
        user_choice = self.generic_org_type
        excluded = [
            DomainRequest.OrganizationChoices.FEDERAL,
            DomainRequest.OrganizationChoices.INTERSTATE,
            DomainRequest.OrganizationChoices.SCHOOL_DISTRICT,
        ]
        return bool(user_choice and user_choice not in excluded)

    def show_about_your_organization(self) -> bool:
        """Show this step if this is a special district or interstate."""
        user_choice = self.generic_org_type
        return user_choice in [
            DomainRequest.OrganizationChoices.SPECIAL_DISTRICT,
            DomainRequest.OrganizationChoices.INTERSTATE,
        ]

    def has_rationale(self) -> bool:
        """Does this domain request have no_other_contacts_rationale?"""
        return bool(self.no_other_contacts_rationale)

    def has_other_contacts(self) -> bool:
        """Does this domain request have other contacts listed?"""
        return self.other_contacts.exists()

    def has_additional_details(self) -> bool:
        """Combines the has_anything_else_text and has_cisa_representative fields,
        then returns if this domain request has either of them."""

        # Split out for linter
        has_details = True

        if self.has_anything_else_text is None or self.has_cisa_representative is None:
            has_details = False
        return has_details

    def is_federal(self) -> Union[bool, None]:
        """Is this domain request for a federal agency?

        generic_org_type can be both null and blank,
        """
        if not self.generic_org_type:
            # generic_org_type is either blank or None, can't answer
            return None
        if self.generic_org_type == DomainRequest.OrganizationChoices.FEDERAL:
            return True
        return False

    def to_dict(self):
        """This is to process to_dict for Domain Information, making it friendly
        to "copy" it

        More information can be found at this- (This used #5)
        https://stackoverflow.com/questions/21925671/convert-django-model-object-to-dict-with-all-of-the-fields-intact/29088221#29088221
        """  # noqa 590
        opts = self._meta
        data = {}
        for field in chain(opts.concrete_fields, opts.private_fields):
            if field.get_internal_type() in ("ForeignKey", "OneToOneField"):
                # get the related instance of the FK value
                fk_id = field.value_from_object(self)
                if fk_id:
                    data[field.name] = field.related_model.objects.get(id=fk_id)
                else:
                    data[field.name] = None
            else:
                data[field.name] = field.value_from_object(self)
        for field in opts.many_to_many:
            data[field.name] = field.value_from_object(self)
        return data

    def get_formatted_cisa_rep_name(self):
        """Returns the cisa representatives name in Western order."""
        names = [n for n in [self.cisa_representative_first_name, self.cisa_representative_last_name] if n]
        return " ".join(names) if names else "Unknown"

    def _is_federal_complete(self):
        # Federal -> "Federal government branch" page can't be empty + Federal Agency selection can't be None
        return not (self.federal_type is None or self.federal_agency is None)

    def _is_interstate_complete(self):
        # Interstate -> "About your organization" page can't be empty
        return self.about_your_organization is not None

    def _is_state_or_territory_complete(self):
        # State -> ""Election office" page can't be empty
        return self.is_election_board is not None

    def _is_tribal_complete(self):
        # Tribal -> "Tribal name" and "Election office" page can't be empty
        return self.tribe_name is not None and self.is_election_board is not None

    def _is_county_complete(self):
        # County -> "Election office" page can't be empty
        return self.is_election_board is not None

    def _is_city_complete(self):
        # City -> "Election office" page can't be empty
        return self.is_election_board is not None

    def _is_special_district_complete(self):
        # Special District -> "Election office" and "About your organization" page can't be empty
        return self.is_election_board is not None and self.about_your_organization is not None

    # Do we still want to test this after creator is autogenerated? Currently it went back to being selectable
    def _is_creator_complete(self):
        return self.creator is not None

    def _is_organization_name_and_address_complete(self):
        return not (
            self.organization_name is None
            and self.address_line1 is None
            and self.city is None
            and self.state_territory is None
            and self.zipcode is None
        )

    def _is_senior_official_complete(self):
        return self.senior_official is not None

    def _is_requested_domain_complete(self):
        return self.requested_domain is not None

    def _is_purpose_complete(self):
        return self.purpose is not None

    def _has_other_contacts_and_filled(self):
        # Other Contacts Radio button is Yes and if all required fields are filled
        return (
            self.has_other_contacts()
            and self.other_contacts.filter(
                first_name__isnull=False,
                last_name__isnull=False,
                title__isnull=False,
                email__isnull=False,
                phone__isnull=False,
            ).exists()
        )

    def _has_no_other_contacts_gives_rationale(self):
        # Other Contacts Radio button is No and a rationale is provided
        return self.has_other_contacts() is False and self.no_other_contacts_rationale is not None

    def _is_other_contacts_complete(self):
        if self._has_other_contacts_and_filled() or self._has_no_other_contacts_gives_rationale():
            return True
        return False

    def _cisa_rep_check(self):
        # Either does not have a CISA rep, OR has a CISA rep + both first name
        # and last name are NOT empty and are NOT an empty string
        to_return = (
            self.has_cisa_representative is True
            and self.cisa_representative_first_name is not None
            and self.cisa_representative_first_name != ""
            and self.cisa_representative_last_name is not None
            and self.cisa_representative_last_name != ""
        ) or self.has_cisa_representative is False

        return to_return

    def _anything_else_radio_button_and_text_field_check(self):
        # Anything else boolean is True + filled text field and it's not an empty string OR the boolean is No
        return (
            self.has_anything_else_text is True and self.anything_else is not None and self.anything_else != ""
        ) or self.has_anything_else_text is False

    def _is_additional_details_complete(self):
        return self._cisa_rep_check() and self._anything_else_radio_button_and_text_field_check()

    def _is_policy_acknowledgement_complete(self):
        return self.is_policy_acknowledged is not None

    def _is_general_form_complete(self, request):
        return (
            self._is_creator_complete()
            and self._is_organization_name_and_address_complete()
            and self._is_senior_official_complete()
            and self._is_requested_domain_complete()
            and self._is_purpose_complete()
            and self._is_other_contacts_complete()
            and self._is_additional_details_complete()
            and self._is_policy_acknowledgement_complete()
        )

    def _form_complete(self, request):
        match self.generic_org_type:
            case DomainRequest.OrganizationChoices.FEDERAL:
                is_complete = self._is_federal_complete()
            case DomainRequest.OrganizationChoices.INTERSTATE:
                is_complete = self._is_interstate_complete()
            case DomainRequest.OrganizationChoices.STATE_OR_TERRITORY:
                is_complete = self._is_state_or_territory_complete()
            case DomainRequest.OrganizationChoices.TRIBAL:
                is_complete = self._is_tribal_complete()
            case DomainRequest.OrganizationChoices.COUNTY:
                is_complete = self._is_county_complete()
            case DomainRequest.OrganizationChoices.CITY:
                is_complete = self._is_city_complete()
            case DomainRequest.OrganizationChoices.SPECIAL_DISTRICT:
                is_complete = self._is_special_district_complete()
            case DomainRequest.OrganizationChoices.SCHOOL_DISTRICT:
                is_complete = True
            case _:
                # NOTE: Shouldn't happen, this is only if somehow they didn't choose an org type
                is_complete = False
        if not is_complete or not self._is_general_form_complete(request):
            return False
        return True

    """The following converted_ property methods get field data from this domain request's portfolio,
    if there is an associated portfolio. If not, they return data from the domain request model."""

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
    def converted_address_line1(self):
        if self.portfolio:
            return self.portfolio.address_line1
        return self.address_line1

    @property
    def converted_address_line2(self):
        if self.portfolio:
            return self.portfolio.address_line2
        return self.address_line2

    @property
    def converted_city(self):
        if self.portfolio:
            return self.portfolio.city
        return self.city

    @property
    def converted_state_territory(self):
        if self.portfolio:
            return self.portfolio.state_territory
        return self.state_territory

    @property
    def converted_urbanization(self):
        if self.portfolio:
            return self.portfolio.urbanization
        return self.urbanization

    @property
    def converted_zipcode(self):
        if self.portfolio:
            return self.portfolio.zipcode
        return self.zipcode

    @property
    def converted_senior_official(self):
        if self.portfolio:
            return self.portfolio.senior_official
        return self.senior_official

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
