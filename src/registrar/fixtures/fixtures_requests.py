from datetime import datetime, timedelta
from django.utils import timezone
import logging
import random
from faker import Faker
from django.db import transaction

from registrar.fixtures.fixtures_portfolios import PortfolioFixture
from registrar.fixtures.fixtures_suborganizations import SuborganizationFixture
from registrar.fixtures.fixtures_users import UserFixture
from registrar.models import User, DomainRequest, DraftDomain, Contact, Website, FederalAgency
from registrar.models.domain import Domain
from registrar.models.portfolio import Portfolio
from registrar.models.suborganization import Suborganization


fake = Faker()
logger = logging.getLogger(__name__)


class DomainRequestFixture:
    """
    Creates domain requests for each user in the database,
    assign portfolios and suborgs.

    Creates 3 in_review requests, one for approving with an expired domain,
    one for approving with a non-expired domain, and one for leaving in in_review.

    Depends on fixtures_portfolios and fixtures_suborganizations.

    Make sure this class' `load` method is called from `handle`
    in management/commands/load.py, then use `./manage.py load`
    to run this code.
    """

    # any fields not specified here will be filled in with fake data or defaults
    # NOTE BENE: each fixture must have `organization_name` for uniqueness!
    # Here is a more complete example as a template:
    # {
    #     "status": "started",
    #     "organization_name": "Example - Just started",
    #     "generic_org_type": "federal",
    #     "federal_agency": None,
    #     "federal_type": None,
    #     "address_line1": None,
    #     "address_line2": None,
    #     "city": None,
    #     "state_territory": None,
    #     "zipcode": None,
    #     "urbanization": None,
    #     "purpose": None,
    #     "anything_else": None,
    #     "is_policy_acknowledged": None,
    #     "senior_official": None,
    #     "other_contacts": [],
    #     "current_websites": [],
    #     "alternative_domains": [],
    # },
    DOMAINREQUESTS = [
        {
            "status": DomainRequest.DomainRequestStatus.STARTED,
            "organization_name": "Example - Finished but not submitted",
        },
        {
            "status": DomainRequest.DomainRequestStatus.SUBMITTED,
            "organization_name": "Example - Submitted but pending investigation",
        },
        {
            "status": DomainRequest.DomainRequestStatus.IN_REVIEW,
            "organization_name": "Example - In investigation",
        },
        {
            "status": DomainRequest.DomainRequestStatus.IN_REVIEW,
            "organization_name": "Example - Approved",
        },
        {
            "status": DomainRequest.DomainRequestStatus.IN_REVIEW,
            "organization_name": "Example - Approved, domain expired",
        },
        {
            "status": DomainRequest.DomainRequestStatus.WITHDRAWN,
            "organization_name": "Example - Withdrawn",
        },
        {
            "status": DomainRequest.DomainRequestStatus.ACTION_NEEDED,
            "organization_name": "Example - Action needed",
        },
        {
            "status": "rejected",
            "organization_name": "Example - Rejected",
        },
    ]

    @classmethod
    def fake_contact(cls):
        return {
            "first_name": fake.first_name(),
            "middle_name": None,
            "last_name": fake.last_name(),
            "title": fake.job(),
            "email": fake.ascii_safe_email(),
            "phone": "201-555-5555",
        }

    @classmethod
    def fake_dot_gov(cls, max_attempts=100):
        """Generate a unique .gov domain name without using an infinite loop."""
        for _ in range(max_attempts):
            fake_name = f"{fake.slug()}.gov"
            if not Domain.objects.filter(name=fake_name).exists():
                return DraftDomain.objects.create(name=fake_name)
        raise RuntimeError(f"Failed to generate a unique .gov domain after {max_attempts} attempts")

    @classmethod
    def fake_expiration_date(cls):
        """Generates a fake expiration date between 0 and 1 year in the future."""
        current_date = timezone.now().date()
        days_in_future = random.randint(0, 365)  # nosec
        return current_date + timedelta(days=days_in_future)

    @classmethod
    def _set_non_foreign_key_fields(cls, request: DomainRequest, request_dict: dict):
        """Helper method used by `load`."""
        request.status = request_dict["status"] if "status" in request_dict else "started"

        # TODO for a future ticket: Allow for more than just "federal" here
        request.generic_org_type = request_dict["generic_org_type"] if "generic_org_type" in request_dict else "federal"
        if request.status != "started":
            # Generate fake data for first_submitted_date and last_submitted_date
            # First generate a random date set to be later than 2020 (or something)
            # (if we just use fake.date() we might get years like 1970 or earlier)
            earliest_date_allowed = datetime(2020, 1, 1).date()
            end_date = datetime.today().date()  # Today's date (latest allowed date)
            days_range = (end_date - earliest_date_allowed).days
            first_submitted_date = earliest_date_allowed + timedelta(days=random.randint(0, days_range))  # nosec

            # Generate a random positive offset to ensure last_submitted_date is later
            # (Start with 1 to ensure at least 1 day difference)
            offset_days = random.randint(1, 30)  # nosec
            last_submitted_date = first_submitted_date + timedelta(days=offset_days)

            # Convert back to strings before assigning
            request.first_submitted_date = first_submitted_date.strftime("%Y-%m-%d")
            request.last_submitted_date = last_submitted_date.strftime("%Y-%m-%d")
        request.federal_type = (
            request_dict["federal_type"]
            if "federal_type" in request_dict
            else random.choice(["executive", "judicial", "legislative"])  # nosec
        )
        request.address_line1 = (
            request_dict["address_line1"] if "address_line1" in request_dict else fake.street_address()
        )
        request.address_line2 = request_dict["address_line2"] if "address_line2" in request_dict else None
        request.city = request_dict["city"] if "city" in request_dict else fake.city()
        request.state_territory = (
            request_dict["state_territory"] if "state_territory" in request_dict else fake.state_abbr()
        )
        request.zipcode = request_dict["zipcode"] if "zipcode" in request_dict else fake.postalcode()
        request.urbanization = request_dict["urbanization"] if "urbanization" in request_dict else None
        request.purpose = request_dict["purpose"] if "purpose" in request_dict else fake.paragraph()
        request.has_cisa_representative = (
            request_dict["has_cisa_representative"] if "has_cisa_representative" in request_dict else True
        )
        request.cisa_representative_email = (
            request_dict["cisa_representative_email"] if "cisa_representative_email" in request_dict else fake.email()
        )
        request.cisa_representative_first_name = (
            request_dict["cisa_representative_first_name"]
            if "cisa_representative_first_name" in request_dict
            else fake.first_name()
        )
        request.cisa_representative_last_name = (
            request_dict["cisa_representative_last_name"]
            if "cisa_representative_last_name" in request_dict
            else fake.last_name()
        )
        request.has_anything_else_text = (
            request_dict["has_anything_else_text"] if "has_anything_else_text" in request_dict else True
        )
        request.anything_else = request_dict["anything_else"] if "anything_else" in request_dict else fake.paragraph()
        request.is_policy_acknowledged = (
            request_dict["is_policy_acknowledged"] if "is_policy_acknowledged" in request_dict else True
        )

    @classmethod
    def _set_foreign_key_fields(cls, request: DomainRequest, request_dict: dict, user: User):
        """Helper method used by `load`."""
        request.investigator = cls._get_investigator(request, request_dict, user)
        request.senior_official = cls._get_senior_official(request, request_dict)
        request.requested_domain = cls._get_requested_domain(request, request_dict)
        request.federal_agency = cls._get_federal_agency(request, request_dict)
        request.portfolio = cls._get_portfolio(request, request_dict)
        request.sub_organization = cls._get_sub_organization(request, request_dict)

    @classmethod
    def _get_investigator(cls, request: DomainRequest, request_dict: dict, user: User):
        if not request.investigator:
            return User.objects.get(username=user.username) if "investigator" in request_dict else None
        return request.investigator

    @classmethod
    def _get_senior_official(cls, request: DomainRequest, request_dict: dict):
        if not request.senior_official:
            if "senior_official" in request_dict and request_dict["senior_official"] is not None:
                return Contact.objects.get_or_create(**request_dict["senior_official"])[0]
            return Contact.objects.create(**cls.fake_contact())
        return request.senior_official

    @classmethod
    def _get_requested_domain(cls, request: DomainRequest, request_dict: dict):
        if not request.requested_domain:
            if "requested_domain" in request_dict and request_dict["requested_domain"] is not None:
                return DraftDomain.objects.get_or_create(name=request_dict["requested_domain"])[0]

            # Generate a unique fake domain
            return cls.fake_dot_gov()
        return request.requested_domain

    @classmethod
    def _get_federal_agency(cls, request: DomainRequest, request_dict: dict):
        if not request.federal_agency:
            if "federal_agency" in request_dict and request_dict["federal_agency"] is not None:
                return FederalAgency.objects.get_or_create(name=request_dict["federal_agency"])[0]
            return random.choice(FederalAgency.objects.all())  # nosec
        return request.federal_agency

    @classmethod
    def _get_portfolio(cls, request: DomainRequest, request_dict: dict):
        if not request.portfolio:
            if "portfolio" in request_dict and request_dict["portfolio"] is not None:
                return Portfolio.objects.get_or_create(name=request_dict["portfolio"])[0]
            return cls._get_random_portfolio()
        return request.portfolio

    @classmethod
    def _get_sub_organization(cls, request: DomainRequest, request_dict: dict):
        if not request.sub_organization:
            if "sub_organization" in request_dict and request_dict["sub_organization"] is not None:
                return Suborganization.objects.get_or_create(name=request_dict["sub_organization"])[0]
            return cls._get_random_sub_organization(request)
        return request.sub_organization

    @classmethod
    def _get_random_portfolio(cls):
        try:
            organization_names = [portfolio["organization_name"] for portfolio in PortfolioFixture.PORTFOLIOS]

            portfolio_options = Portfolio.objects.filter(organization_name__in=organization_names)
            return random.choice(portfolio_options) if portfolio_options.exists() else None  # nosec
        except Exception as e:
            logger.warning(f"Expected fixture portfolio, did not find it: {e}")
            return None

    @classmethod
    def _get_random_sub_organization(cls, request):
        try:
            # Filter Suborganizations by the request's portfolio
            portfolio_suborganizations = Suborganization.objects.filter(portfolio=request.portfolio)

            # Select a suborg that's defined in the fixtures
            suborganization_names = [suborg["name"] for suborg in SuborganizationFixture.SUBORGS]

            # Further filter by names in suborganization_names
            suborganization_options = portfolio_suborganizations.filter(name__in=suborganization_names)

            # Randomly choose one if any exist
            return random.choice(suborganization_options) if suborganization_options.exists() else None  # nosec
        except Exception as e:
            logger.warning(f"Expected fixture sub_organization, did not find it: {e}")
            return None

    @classmethod
    def _set_many_to_many_relations(cls, request: DomainRequest, request_dict: dict):
        """Helper method used by `load`."""
        if "other_contacts" in request_dict:
            for contact in request_dict["other_contacts"]:
                request.other_contacts.add(Contact.objects.get_or_create(**contact)[0])
        elif not request.other_contacts.exists():
            other_contacts = [
                Contact.objects.create(**cls.fake_contact()) for _ in range(random.randint(1, 3))  # nosec
            ]
            request.other_contacts.add(*other_contacts)

        if "current_websites" in request_dict:
            for website in request_dict["current_websites"]:
                request.current_websites.add(Website.objects.get_or_create(website=website)[0])
        elif not request.current_websites.exists():
            current_websites = [
                Website.objects.create(website=fake.uri()) for _ in range(random.randint(0, 3))  # nosec
            ]
            request.current_websites.add(*current_websites)

        if "alternative_domains" in request_dict:
            for domain in request_dict["alternative_domains"]:
                request.alternative_domains.add(Website.objects.get_or_create(website=domain)[0])
        elif not request.alternative_domains.exists():
            alternative_domains = [
                Website.objects.create(website=cls.fake_dot_gov()) for _ in range(random.randint(0, 3))  # nosec
            ]
            request.alternative_domains.add(*alternative_domains)

    @classmethod
    def load(cls):
        """Creates domain requests for each user in the database."""
        logger.info("Going to load %s domain requests" % len(cls.DOMAINREQUESTS))

        # Lumped under .atomic to ensure we don't make redundant DB calls.
        # This bundles them all together, and then saves it in a single call.
        # The atomic block will cause the code to stop executing if one instance in the
        # nested iteration fails, which will cause an early exit and make it hard to debug.
        # Comment out with transaction.atomic() when debugging.
        with transaction.atomic():
            try:
                # Get the usernames of users created in the UserFixture
                created_usernames = [user_data["username"] for user_data in UserFixture.ADMINS + UserFixture.STAFF]

                # Filter users to only include those created by the fixture
                users = list(User.objects.filter(username__in=created_usernames))
            except Exception as e:
                logger.warning(e)
                return

            cls._create_domain_requests(users)

    @classmethod
    def _create_domain_requests(cls, users):  # noqa: C901
        """Creates DomainRequests given a list of users."""
        total_domain_requests_to_make = 10000

        # Check if the database is already populated with the desired
        # number of entries.
        # (Prevents re-adding more entries to an already populated database,
        # which happens when restarting Docker src)
        domain_requests_already_made = DomainRequest.objects.count()

        domain_requests_to_create = []
        if domain_requests_already_made < total_domain_requests_to_make:
            for user in users:
                for request_data in cls.DOMAINREQUESTS:
                    # Prepare DomainRequest objects
                    try:
                        domain_request = DomainRequest(
                            creator=user,
                            organization_name=request_data["organization_name"],
                        )
                        cls._set_non_foreign_key_fields(domain_request, request_data)
                        cls._set_foreign_key_fields(domain_request, request_data, user)
                        domain_requests_to_create.append(domain_request)
                    except Exception as e:
                        logger.warning(e)

            num_additional_requests_to_make = (
                total_domain_requests_to_make - domain_requests_already_made - len(domain_requests_to_create)
            )
            if num_additional_requests_to_make > 0:
                for _ in range(num_additional_requests_to_make):
                    random_user = random.choice(users)  # nosec
                    try:
                        random_request_type = random.choice(cls.DOMAINREQUESTS)  # nosec
                        # Prepare DomainRequest objects
                        domain_request = DomainRequest(
                            creator=random_user,
                            organization_name=random_request_type["organization_name"],
                        )
                        cls._set_non_foreign_key_fields(domain_request, random_request_type)
                        cls._set_foreign_key_fields(domain_request, random_request_type, random_user)
                        domain_requests_to_create.append(domain_request)
                    except Exception as e:
                        logger.warning(f"Error creating random domain request: {e}")

        # Bulk create domain requests
        cls._bulk_create_requests(domain_requests_to_create)

        # Now many-to-many relationships
        for domain_request in domain_requests_to_create:
            try:
                cls._set_many_to_many_relations(domain_request, request_data)
            except Exception as e:
                logger.warning(e)

    @classmethod
    def _bulk_create_requests(cls, domain_requests_to_create):
        """Bulk create domain requests."""
        if len(domain_requests_to_create) > 0:
            try:
                DomainRequest.objects.bulk_create(domain_requests_to_create)
                logger.info(f"Successfully created {len(domain_requests_to_create)} requests.")
            except Exception as e:
                logger.error(f"Unexpected error during requests bulk creation: {e}")
