import os
import logging
from contextlib import contextmanager
import random
from string import ascii_uppercase
import uuid
from django.test import TestCase
from unittest.mock import MagicMock, Mock, patch
from typing import List, Dict
from django.contrib.sessions.middleware import SessionMiddleware
from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.utils.timezone import make_aware
from datetime import date, datetime, timedelta
from django.utils import timezone
from django.utils.html import strip_spaces_between_tags

from registrar.models import (
    Contact,
    DraftDomain,
    Website,
    DomainRequest,
    DomainInvitation,
    User,
    UserGroup,
    DomainInformation,
    PublicContact,
    Domain,
    FederalAgency,
    UserPortfolioPermission,
    Portfolio,
    PortfolioInvitation,
)
from epplibwrapper import (
    commands,
    common,
    extensions,
    info,
    RegistryError,
    ErrorCode,
    responses,
)
from registrar.models.suborganization import Suborganization
from registrar.models.utility.portfolio_helper import UserPortfolioPermissionChoices, UserPortfolioRoleChoices
from registrar.models.user_domain_role import UserDomainRole

from registrar.models.utility.contact_error import ContactError, ContactErrorCodes

from api.tests.common import less_console_noise_decorator

logger = logging.getLogger(__name__)


def get_handlers():
    """Obtain pointers to all StreamHandlers."""
    handlers = {}

    rootlogger = logging.getLogger()
    for h in rootlogger.handlers:
        if isinstance(h, logging.StreamHandler):
            handlers[h.name] = h

    for logger in logging.Logger.manager.loggerDict.values():
        if not isinstance(logger, logging.PlaceHolder):
            for h in logger.handlers:
                if isinstance(h, logging.StreamHandler):
                    handlers[h.name] = h

    return handlers


@contextmanager
def less_console_noise(output_stream=None):
    """
    Context manager to use in tests to silence console logging.

    This is helpful on tests which trigger console messages
    (such as errors) which are normal and expected.

    It can easily be removed to debug a failing test.

    Arguments:
        `output_stream`: a stream to redirect every handler to. If it's
        not provided, use /dev/null.
    """
    restore = {}
    handlers = get_handlers()
    if output_stream is None:
        output_stream = open(os.devnull, "w")

    # redirect all the streams
    for handler in handlers.values():
        prior = handler.setStream(output_stream)
        restore[handler.name] = prior
    try:
        # run the test
        yield
    finally:
        # restore the streams
        for handler in handlers.values():
            handler.setStream(restore[handler.name])
        if output_stream is None:
            # we opened output_stream so we have to close it
            output_stream.close()


def get_time_aware_date(date=datetime(2023, 11, 1)):
    """Returns a time aware date"""
    return timezone.make_aware(date)


def normalize_html(html):
    """Normalize HTML by removing newlines and extra spaces."""
    return strip_spaces_between_tags(" ".join(html.split()))


class GenericTestHelper(TestCase):
    """A helper class that contains various helper functions for TestCases"""

    def __init__(self, admin, model=None, url=None, user=None, factory=None, client=None, **kwargs):
        """
        Parameters:
            admin (ModelAdmin): The Django ModelAdmin instance associated with the model.
            model (django.db.models.Model, optional): The Django model associated with the admin page.
            url (str, optional): The URL of the Django Admin page to test.
            user (User, optional): The Django User who is making the request.
            factory (RequestFactory, optional): An instance of Django's RequestFactory.
        """
        super().__init__()
        self.factory = factory
        self.user = user
        self.admin = admin
        self.model = model
        self.url = url
        self.client = client

    def assert_response_contains_distinct_values(self, response, expected_values):
        """
        Asserts that each specified value appears exactly once in the response.

        This method iterates over a list of tuples, where each tuple contains a field name
        and its expected value. It then performs an assertContains check for each value,
        ensuring that each value appears exactly once in the response.

        Parameters:
        - response: The HttpResponse object to inspect.
        - expected_values: A list of tuples, where each tuple contains:
            - field: The name of the field (used for subTest identification).
            - value: The expected value to check for in the response.

        Example usage:
        expected_values = [
            ("title", "Treat inspector</td>"),
            ("email", "meoward.jones@igorville.gov</td>"),
        ]
        self.assert_response_contains_distinct_values(response, expected_values)
        """
        for field, value in expected_values:
            with self.subTest(field=field, expected_value=value):
                self.assertContains(response, value, count=1)

    def assert_table_sorted(self, o_index, sort_fields):
        """
        This helper function validates the sorting functionality of a Django Admin table view.

        It creates a mock HTTP GET request to the provided URL with a specific ordering parameter,
        and compares the returned sorted queryset with the expected sorted queryset.

        Parameters:
        o_index (str): The index of the field in the table to sort by. This is passed as a string
                    to the 'o' parameter in the GET request.
        sort_fields (tuple): The fields of the model to sort by. These fields are used to generate
                            the expected sorted queryset.


        Example Usage:
        ```
        self.assert_sort_helper(
            "1", ("domain__name",)
        )
        ```

        The function asserts that the returned sorted queryset from the admin page matches the
        expected sorted queryset. If the assertion fails, it means the sorting functionality
        on the admin page is not working as expected.
        """
        # 'o' is a search param defined by the current index position in the
        # table, plus one.
        dummy_request = self.factory.get(
            self.url,
            {"o": o_index},
        )
        dummy_request.user = self.user

        # Mock a user request
        dummy_request = self._mock_user_request_for_factory(dummy_request)

        expected_sort_order = list(self.model.objects.order_by(*sort_fields))

        # Use changelist_view to get the sorted queryset
        response = self.admin.changelist_view(dummy_request)
        response.render()  # Render the response before accessing its content
        returned_sort_order = list(response.context_data["cl"].result_list)

        self.assertEqual(expected_sort_order, returned_sort_order)

    @classmethod
    def _mock_user_request_for_factory(self, request):
        """Adds sessionmiddleware when using factory to associate session information"""
        middleware = SessionMiddleware(lambda req: req)
        middleware.process_request(request)
        request.session.save()
        return request

    def get_table_delete_confirmation_page(self, selected_across: str, index: str):
        """
        Grabs the response for the delete confirmation page (generated from the actions toolbar).
        selected_across and index must both be numbers encoded as str, e.g. "0" rather than 0
        """

        response = self.client.post(
            self.url,
            {"action": "delete_selected", "select_across": selected_across, "index": index, "_selected_action": "23"},
            follow=True,
        )
        return response


class MockUserLogin:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_anonymous:
            user = None
            UserModel = get_user_model()
            username = "Testy"
            args = {
                UserModel.USERNAME_FIELD: username,
            }
            user, _ = UserModel.objects.get_or_create(**args)
            user.is_staff = True
            # Create or retrieve the group
            group, _ = UserGroup.objects.get_or_create(name="full_access_group")
            # Add the user to the group
            user.groups.set([group])
            user.save()
            backend = settings.AUTHENTICATION_BACKENDS[-1]
            login(request, user, backend=backend)

        response = self.get_response(request)
        return response


class MockSESClient(Mock):
    EMAILS_SENT: List[Dict] = []

    def send_email(self, *args, **kwargs):
        self.EMAILS_SENT.append({"args": args, "kwargs": kwargs})


class AuditedAdminMockData:
    """Creates simple data mocks for AuditedAdminTest.
    Can likely be more generalized, but the primary purpose of this class is to simplify
    mock data creation, especially for lists of items,
    by making the assumption that for most use cases we don't have to worry about
    data 'accuracy' ('testy 2' is not an accurate first_name for example), we just care about
    implementing some kind of patterning, especially with lists of items.

    Two variables are used across multiple functions:

    *item_name* - Used in patterning. Will be appended en masse to multiple str fields,
    like first_name. For example, item_name 'egg' will return a user object of:

    first_name: 'egg first_name:user',
    last_name: 'egg last_name:user',
    username: 'egg username:user'

    where 'user' is the short_hand

    *short_hand* - Used in patterning. Certain fields will have ':{shorthand}' appended to it,
    as a way to optionally include metadata in the str itself. Can be further expanded on.
    Came from a bug where different querysets used in testing would effectively be 'anonymized', wherein
    it would only display a list of types, but not include the variable name.
    """  # noqa

    # Constants for different domain object types
    INFORMATION = "information"
    DOMAIN_REQUEST = "domain_request"
    INVITATION = "invitation"

    def dummy_user(self, item_name, short_hand):
        """Creates a dummy user object,
        but with a shorthand and support for multiple"""
        user = User.objects.get_or_create(
            first_name="{} first_name:{}".format(item_name, short_hand),
            last_name="{} last_name:{}".format(item_name, short_hand),
            username="{} username:{}".format(item_name + str(uuid.uuid4())[:8], short_hand),
            is_staff=True,
        )[0]
        return user

    def dummy_contact(self, item_name, short_hand):
        """Creates a dummy contact object"""
        contact = Contact.objects.get_or_create(
            first_name="{} first_name:{}".format(item_name, short_hand),
            last_name="{} last_name:{}".format(item_name, short_hand),
            title="{} title:{}".format(item_name, short_hand),
            email="{}testy@town.com".format(item_name),
            phone="(555) 555 5555",
        )[0]
        return contact

    def dummy_draft_domain(self, item_name, prebuilt=False):
        """
        Creates a dummy DraftDomain object
        Args:
            item_name (str): Value for 'name' in a DraftDomain object.
            prebuilt (boolean): Determines return type.
        Returns:
            DraftDomain: Where name = 'item_name'. If prebuilt = True, then
            name will be "city{}.gov".format(item_name).
        """
        if prebuilt:
            item_name = "city{}.gov".format(item_name)
        return DraftDomain.objects.get_or_create(name=item_name)[0]

    def dummy_domain(self, item_name, prebuilt=False):
        """
        Creates a dummy domain object
        Args:
            item_name (str): Value for 'name' in a Domain object.
            prebuilt (boolean): Determines return type.
        Returns:
            Domain: Where name = 'item_name'. If prebuilt = True, then
            domain name will be "city{}.gov".format(item_name).
        """
        if prebuilt:
            item_name = "city{}.gov".format(item_name)
        return Domain.objects.get_or_create(name=item_name)[0]

    def dummy_website(self, item_name):
        """
        Creates a dummy website object
        Args:
            item_name (str): Value for 'website' in a Website object.
        Returns:
            Website: Where website = 'item_name'.
        """
        return Website.objects.get_or_create(website=item_name)[0]

    def dummy_alt(self, item_name):
        """
        Creates a dummy website object for alternates
        Args:
            item_name (str): Value for 'website' in a Website object.
        Returns:
            Website: Where website = "cityalt{}.gov".format(item_name).
        """
        return self.dummy_website(item_name="cityalt{}.gov".format(item_name))

    def dummy_current(self, item_name):
        """
        Creates a dummy website object for current
        Args:
            item_name (str): Value for 'website' in a Website object.
            prebuilt (boolean): Determines return type.
        Returns:
            Website: Where website = "city{}.gov".format(item_name)
        """
        return self.dummy_website(item_name="city{}.com".format(item_name))

    def get_common_domain_arg_dictionary(
        self,
        item_name,
        org_type="federal",
        federal_type="executive",
        purpose="Purpose of the site",
    ):
        """
        Generates a generic argument dict for most domains
        Args:
            item_name (str): A shared str value appended to first_name, last_name,
            organization_name, address_line1, address_line2,
            title, email, and username.

            org_type (str - optional): Sets a domains org_type

            federal_type (str - optional): Sets a domains federal_type

            purpose (str - optional): Sets a domains purpose
        Returns:
            Dictionary: {
                generic_org_type: str,
                federal_type: str,
                purpose: str,
                organization_name: str = "{} organization".format(item_name),
                address_line1: str = "{} address_line1".format(item_name),
                address_line2: str = "{} address_line2".format(item_name),
                is_policy_acknowledged: boolean = True,
                state_territory: str = "NY",
                zipcode: str = "10002",
                about_your_organization: str = "e-Government",
                anything_else: str = "There is more",
                senior_official: Contact = self.dummy_contact(item_name, "senior_official"),
                creator: User = self.dummy_user(item_name, "creator"),
            }
        """  # noqa
        creator = self.dummy_user(item_name, "creator")
        common_args = dict(
            generic_org_type=org_type,
            federal_type=federal_type,
            purpose=purpose,
            organization_name="{} organization".format(item_name),
            address_line1="{} address_line1".format(item_name),
            address_line2="{} address_line2".format(item_name),
            is_policy_acknowledged=True,
            state_territory="NY",
            zipcode="10002",
            about_your_organization="e-Government",
            anything_else="There is more",
            senior_official=self.dummy_contact(item_name, "senior_official"),
            creator=creator,
        )
        return common_args

    def dummy_kwarg_boilerplate(
        self,
        domain_type,
        item_name,
        status=DomainRequest.DomainRequestStatus.STARTED,
        org_type="federal",
        federal_type="executive",
        purpose="Purpose of the site",
    ):
        """
        Returns a prebuilt kwarg dictionary for DomainRequest,
        DomainInformation, or DomainInvitation.
        Args:
            domain_type (str): is either 'domain_request', 'information',
            or 'invitation'.

            item_name (str): A shared str value appended to first_name, last_name,
            organization_name, address_line1, address_line2,
            title, email, and username.

            status (str - optional): Defines the status for DomainRequest,
            e.g. DomainRequest.DomainRequestStatus.STARTED

            org_type (str - optional): Sets a domains org_type

            federal_type (str - optional): Sets a domains federal_type

            purpose (str - optional): Sets a domains purpose
        Returns:
            dict: Returns a dictionary structurally consistent with the expected input
            of either DomainRequest, DomainInvitation, or DomainInformation
            based on the 'domain_type' field.
        """  # noqa
        common_args = self.get_common_domain_arg_dictionary(item_name, org_type, federal_type, purpose)
        full_arg_dict = None
        match domain_type:
            case self.DOMAIN_REQUEST:
                full_arg_dict = dict(
                    **common_args,
                    requested_domain=self.dummy_draft_domain(item_name),
                    investigator=self.dummy_user(item_name, "investigator"),
                    status=status,
                )
            case self.INFORMATION:
                domain_req = self.create_full_dummy_domain_request(item_name)
                full_arg_dict = dict(
                    **common_args,
                    domain=self.dummy_domain(item_name, True),
                    domain_request=domain_req,
                )
            case self.INVITATION:
                full_arg_dict = dict(
                    email="test_mail@mail.com",
                    domain=self.dummy_domain(item_name, True),
                    status=DomainInvitation.DomainInvitationStatus.INVITED,
                )
        return full_arg_dict

    def create_full_dummy_domain_request(self, item_name, status=DomainRequest.DomainRequestStatus.STARTED):
        """Creates a dummy domain request object"""
        domain_request_kwargs = self.dummy_kwarg_boilerplate(self.DOMAIN_REQUEST, item_name, status)
        domain_request = DomainRequest.objects.get_or_create(**domain_request_kwargs)[0]
        return domain_request

    def create_full_dummy_domain_information(self, item_name, status=DomainRequest.DomainRequestStatus.STARTED):
        """Creates a dummy domain information object"""
        domain_request_kwargs = self.dummy_kwarg_boilerplate(self.INFORMATION, item_name, status)
        domain_request = DomainInformation.objects.get_or_create(**domain_request_kwargs)[0]
        return domain_request

    def create_full_dummy_domain_invitation(self, item_name, status=DomainRequest.DomainRequestStatus.STARTED):
        """Creates a dummy domain invitation object"""
        domain_request_kwargs = self.dummy_kwarg_boilerplate(self.INVITATION, item_name, status)
        domain_request = DomainInvitation.objects.get_or_create(**domain_request_kwargs)[0]

        return domain_request

    def create_full_dummy_domain_object(
        self,
        domain_type,
        item_name,
        has_other_contacts=True,
        has_current_website=True,
        has_alternative_gov_domain=True,
        status=DomainRequest.DomainRequestStatus.STARTED,
    ):
        """A helper to create a dummy domain request object"""
        domain_request = None
        match domain_type:
            case self.DOMAIN_REQUEST:
                domain_request = self.create_full_dummy_domain_request(item_name, status)
            case self.INVITATION:
                domain_request = self.create_full_dummy_domain_invitation(item_name, status)
            case self.INFORMATION:
                domain_request = self.create_full_dummy_domain_information(item_name, status)
            case _:
                raise ValueError("Invalid domain_type, must conform to given constants")

        if has_other_contacts and domain_type != self.INVITATION:
            other = self.dummy_contact(item_name, "other")
            domain_request.other_contacts.add(other)
        if has_current_website and domain_type == self.DOMAIN_REQUEST:
            current = self.dummy_current(item_name)
            domain_request.current_websites.add(current)
        if has_alternative_gov_domain and domain_type == self.DOMAIN_REQUEST:
            alt = self.dummy_alt(item_name)
            domain_request.alternative_domains.add(alt)

        return domain_request


class MockDb(TestCase):

    @classmethod
    @less_console_noise_decorator
    def sharedSetUp(cls):
        cls.mock_client_class = MagicMock()
        cls.mock_client = cls.mock_client_class.return_value
        username = "test_user"
        first_name = "First"
        last_name = "Last"
        email = "info@example.com"
        title = "title"
        phone = "8080102431"
        cls.user = get_user_model().objects.create(
            username=username, first_name=first_name, last_name=last_name, email=email, title=title, phone=phone
        )
        cls.meoward_user = get_user_model().objects.create(
            username="meoward_username", first_name="first_meoward", last_name="last_meoward", email="meoward@rocks.com"
        )
        cls.lebowski_user = get_user_model().objects.create(
            username="big_lebowski", first_name="big", last_name="lebowski", email="big_lebowski@dude.co"
        )
        cls.tired_user = get_user_model().objects.create(
            username="ministry_of_bedtime", first_name="tired", last_name="sleepy", email="tired_sleepy@igorville.gov"
        )
        # Custom superuser and staff so that these do not conflict with what may be defined on what implements this.
        cls.custom_superuser = create_superuser(
            username="cold_superuser", first_name="cold", last_name="icy", email="icy_superuser@igorville.gov"
        )
        cls.custom_staffuser = create_user(
            username="warm_staff", first_name="warm", last_name="cozy", email="cozy_staffuser@igorville.gov"
        )

        cls.federal_agency_1, _ = FederalAgency.objects.get_or_create(agency="World War I Centennial Commission")
        cls.federal_agency_2, _ = FederalAgency.objects.get_or_create(agency="Armed Forces Retirement Home")
        cls.federal_agency_3, _ = FederalAgency.objects.get_or_create(
            agency="Portfolio 1 Federal Agency", federal_type="executive"
        )

        cls.portfolio_1, _ = Portfolio.objects.get_or_create(
            creator=cls.custom_superuser, federal_agency=cls.federal_agency_3, organization_type="federal"
        )

        cls.suborganization_1, _ = Suborganization.objects.get_or_create(
            name="SubOrg 1",
            portfolio=cls.portfolio_1,
            city="Nashville",
            state_territory="TN",
        )

        current_date = get_time_aware_date(datetime(2024, 4, 2))
        # Create start and end dates using timedelta

        cls.end_date = current_date + timedelta(days=2)
        cls.start_date = current_date - timedelta(days=2)

        cls.domain_1, _ = Domain.objects.get_or_create(
            name="cdomain1.gov", state=Domain.State.READY, first_ready=get_time_aware_date(datetime(2024, 4, 2))
        )
        cls.domain_2, _ = Domain.objects.get_or_create(name="adomain2.gov", state=Domain.State.DNS_NEEDED)
        cls.domain_3, _ = Domain.objects.get_or_create(name="ddomain3.gov", state=Domain.State.ON_HOLD)
        cls.domain_4, _ = Domain.objects.get_or_create(name="bdomain4.gov", state=Domain.State.UNKNOWN)
        cls.domain_5, _ = Domain.objects.get_or_create(
            name="bdomain5.gov", state=Domain.State.DELETED, deleted=get_time_aware_date(datetime(2023, 11, 1))
        )
        cls.domain_6, _ = Domain.objects.get_or_create(
            name="bdomain6.gov", state=Domain.State.DELETED, deleted=get_time_aware_date(datetime(1980, 10, 16))
        )
        cls.domain_7, _ = Domain.objects.get_or_create(
            name="xdomain7.gov", state=Domain.State.DELETED, deleted=get_time_aware_date(datetime(2024, 4, 2))
        )
        cls.domain_8, _ = Domain.objects.get_or_create(
            name="sdomain8.gov", state=Domain.State.DELETED, deleted=get_time_aware_date(datetime(2024, 4, 2))
        )
        # We use timezone.make_aware to sync to server time a datetime object with the current date (using date.today())
        # and a specific time (using datetime.min.time()).
        # Deleted yesterday
        cls.domain_9, _ = Domain.objects.get_or_create(
            name="zdomain9.gov",
            state=Domain.State.DELETED,
            deleted=get_time_aware_date(datetime(2024, 4, 1)),
        )
        # ready tomorrow
        cls.domain_10, _ = Domain.objects.get_or_create(
            name="adomain10.gov",
            state=Domain.State.READY,
            first_ready=get_time_aware_date(datetime(2024, 4, 3)),
        )
        cls.domain_11, _ = Domain.objects.get_or_create(
            name="cdomain11.gov", state=Domain.State.READY, first_ready=get_time_aware_date(datetime(2024, 4, 2))
        )
        cls.domain_12, _ = Domain.objects.get_or_create(
            name="zdomain12.gov", state=Domain.State.READY, first_ready=get_time_aware_date(datetime(2024, 4, 2))
        )

        cls.domain_information_1, _ = DomainInformation.objects.get_or_create(
            creator=cls.user,
            domain=cls.domain_1,
            generic_org_type="federal",
            federal_agency=cls.federal_agency_1,
            federal_type="executive",
            is_election_board=False,
            portfolio=cls.portfolio_1,
        )
        cls.domain_information_2, _ = DomainInformation.objects.get_or_create(
            creator=cls.user,
            domain=cls.domain_2,
            generic_org_type="interstate",
            is_election_board=True,
            portfolio=cls.portfolio_1,
        )
        cls.domain_information_3, _ = DomainInformation.objects.get_or_create(
            creator=cls.user,
            domain=cls.domain_3,
            generic_org_type="federal",
            federal_agency=cls.federal_agency_2,
            is_election_board=False,
        )
        cls.domain_information_4, _ = DomainInformation.objects.get_or_create(
            creator=cls.user,
            domain=cls.domain_4,
            generic_org_type="federal",
            federal_agency=cls.federal_agency_2,
            is_election_board=False,
        )
        cls.domain_information_5, _ = DomainInformation.objects.get_or_create(
            creator=cls.user,
            domain=cls.domain_5,
            generic_org_type="federal",
            federal_agency=cls.federal_agency_2,
            is_election_board=False,
        )
        cls.domain_information_6, _ = DomainInformation.objects.get_or_create(
            creator=cls.user,
            domain=cls.domain_6,
            generic_org_type="federal",
            federal_agency=cls.federal_agency_2,
            is_election_board=False,
        )
        cls.domain_information_7, _ = DomainInformation.objects.get_or_create(
            creator=cls.user,
            domain=cls.domain_7,
            generic_org_type="federal",
            federal_agency=cls.federal_agency_2,
            is_election_board=False,
        )
        cls.domain_information_8, _ = DomainInformation.objects.get_or_create(
            creator=cls.user,
            domain=cls.domain_8,
            generic_org_type="federal",
            federal_agency=cls.federal_agency_2,
            is_election_board=False,
        )
        cls.domain_information_9, _ = DomainInformation.objects.get_or_create(
            creator=cls.user,
            domain=cls.domain_9,
            generic_org_type="federal",
            federal_agency=cls.federal_agency_2,
            is_election_board=False,
        )
        cls.domain_information_10, _ = DomainInformation.objects.get_or_create(
            creator=cls.user,
            domain=cls.domain_10,
            generic_org_type="federal",
            federal_agency=cls.federal_agency_2,
            is_election_board=False,
        )
        cls.domain_information_11, _ = DomainInformation.objects.get_or_create(
            creator=cls.user,
            domain=cls.domain_11,
            generic_org_type="federal",
            federal_agency=cls.federal_agency_1,
            federal_type="executive",
            is_election_board=False,
        )
        cls.domain_information_12, _ = DomainInformation.objects.get_or_create(
            creator=cls.user,
            domain=cls.domain_12,
            generic_org_type="interstate",
            is_election_board=False,
        )

        _, created = UserDomainRole.objects.get_or_create(
            user=cls.meoward_user, domain=cls.domain_1, role=UserDomainRole.Roles.MANAGER
        )

        _, created = UserDomainRole.objects.get_or_create(
            user=cls.user, domain=cls.domain_1, role=UserDomainRole.Roles.MANAGER
        )

        _, created = UserDomainRole.objects.get_or_create(
            user=cls.lebowski_user, domain=cls.domain_1, role=UserDomainRole.Roles.MANAGER
        )

        _, created = UserDomainRole.objects.get_or_create(
            user=cls.meoward_user, domain=cls.domain_2, role=UserDomainRole.Roles.MANAGER
        )

        _, created = UserDomainRole.objects.get_or_create(
            user=cls.meoward_user, domain=cls.domain_11, role=UserDomainRole.Roles.MANAGER
        )

        _, created = UserDomainRole.objects.get_or_create(
            user=cls.meoward_user, domain=cls.domain_12, role=UserDomainRole.Roles.MANAGER
        )

        _, created = DomainInvitation.objects.get_or_create(
            email=cls.meoward_user.email,
            domain=cls.domain_1,
            status=DomainInvitation.DomainInvitationStatus.RETRIEVED,
        )

        _, created = DomainInvitation.objects.get_or_create(
            email=cls.meoward_user.email,
            domain=cls.domain_11,
            status=DomainInvitation.DomainInvitationStatus.RETRIEVED,
        )

        _, created = DomainInvitation.objects.get_or_create(
            email="woofwardthethird@rocks.com",
            domain=cls.domain_1,
            status=DomainInvitation.DomainInvitationStatus.INVITED,
        )

        _, created = DomainInvitation.objects.get_or_create(
            email="squeaker@rocks.com", domain=cls.domain_2, status=DomainInvitation.DomainInvitationStatus.INVITED
        )

        _, created = DomainInvitation.objects.get_or_create(
            email="squeaker@rocks.com", domain=cls.domain_10, status=DomainInvitation.DomainInvitationStatus.INVITED
        )

        cls.portfolio_invitation_1, _ = PortfolioInvitation.objects.get_or_create(
            email=cls.meoward_user.email,
            portfolio=cls.portfolio_1,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
            additional_permissions=[UserPortfolioPermissionChoices.EDIT_MEMBERS],
        )

        cls.portfolio_invitation_2, _ = PortfolioInvitation.objects.get_or_create(
            email=cls.lebowski_user.email,
            portfolio=cls.portfolio_1,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
            additional_permissions=[UserPortfolioPermissionChoices.VIEW_MEMBERS],
        )

        cls.portfolio_invitation_3, _ = PortfolioInvitation.objects.get_or_create(
            email=cls.tired_user.email,
            portfolio=cls.portfolio_1,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
            additional_permissions=[UserPortfolioPermissionChoices.VIEW_ALL_REQUESTS],
        )

        cls.portfolio_invitation_4, _ = PortfolioInvitation.objects.get_or_create(
            email=cls.custom_superuser.email,
            portfolio=cls.portfolio_1,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
                UserPortfolioPermissionChoices.VIEW_ALL_REQUESTS,
                UserPortfolioPermissionChoices.EDIT_REQUESTS,
            ],
        )

        cls.portfolio_invitation_5, _ = PortfolioInvitation.objects.get_or_create(
            email=cls.custom_staffuser.email,
            portfolio=cls.portfolio_1,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
        )

        # Add some invitations that we never retireve
        PortfolioInvitation.objects.get_or_create(
            email="nonexistentmember_1@igorville.gov",
            portfolio=cls.portfolio_1,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
            additional_permissions=[UserPortfolioPermissionChoices.EDIT_MEMBERS],
        )

        PortfolioInvitation.objects.get_or_create(
            email="nonexistentmember_2@igorville.gov",
            portfolio=cls.portfolio_1,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
            additional_permissions=[UserPortfolioPermissionChoices.VIEW_MEMBERS],
        )

        PortfolioInvitation.objects.get_or_create(
            email="nonexistentmember_3@igorville.gov",
            portfolio=cls.portfolio_1,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
            additional_permissions=[UserPortfolioPermissionChoices.VIEW_ALL_REQUESTS],
        )

        PortfolioInvitation.objects.get_or_create(
            email="nonexistentmember_4@igorville.gov",
            portfolio=cls.portfolio_1,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
            additional_permissions=[
                UserPortfolioPermissionChoices.VIEW_MEMBERS,
                UserPortfolioPermissionChoices.EDIT_MEMBERS,
                UserPortfolioPermissionChoices.VIEW_ALL_REQUESTS,
                UserPortfolioPermissionChoices.EDIT_REQUESTS,
            ],
        )

        PortfolioInvitation.objects.get_or_create(
            email="nonexistentmember_5@igorville.gov",
            portfolio=cls.portfolio_1,
            roles=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN],
        )

        with less_console_noise():
            cls.domain_request_1 = completed_domain_request(
                status=DomainRequest.DomainRequestStatus.STARTED,
                name="city1.gov",
            )
            cls.domain_request_2 = completed_domain_request(
                status=DomainRequest.DomainRequestStatus.IN_REVIEW,
                name="city2.gov",
                portfolio=cls.portfolio_1,
                sub_organization=cls.suborganization_1,
            )
            cls.domain_request_3 = completed_domain_request(
                status=DomainRequest.DomainRequestStatus.STARTED,
                name="city3.gov",
                portfolio=cls.portfolio_1,
            )
            cls.domain_request_4 = completed_domain_request(
                status=DomainRequest.DomainRequestStatus.STARTED,
                name="city4.gov",
                is_election_board=True,
                generic_org_type="city",
            )
            cls.domain_request_5 = completed_domain_request(
                status=DomainRequest.DomainRequestStatus.APPROVED,
                name="city5.gov",
                requested_suborganization="requested_suborg",
                suborganization_city="SanFran",
                suborganization_state_territory="CA",
            )
            cls.domain_request_6 = completed_domain_request(
                status=DomainRequest.DomainRequestStatus.STARTED,
                name="city6.gov",
                portfolio=cls.portfolio_1,
            )
            cls.domain_request_3.submit()
            cls.domain_request_4.submit()
            cls.domain_request_6.submit()

            other, _ = Contact.objects.get_or_create(
                first_name="Testy1232",
                last_name="Tester24",
                title="Another Tester",
                email="te2@town.com",
                phone="(555) 555 5557",
            )
            other_2, _ = Contact.objects.get_or_create(
                first_name="Meow",
                last_name="Tester24",
                title="Another Tester",
                email="te2@town.com",
                phone="(555) 555 5557",
            )
            website, _ = Website.objects.get_or_create(website="igorville.gov")
            website_2, _ = Website.objects.get_or_create(website="cheeseville.gov")
            website_3, _ = Website.objects.get_or_create(website="https://www.example.com")
            website_4, _ = Website.objects.get_or_create(website="https://www.example2.com")

            cls.domain_request_3.other_contacts.add(other, other_2)
            cls.domain_request_3.alternative_domains.add(website, website_2)
            cls.domain_request_3.current_websites.add(website_3, website_4)
            cls.domain_request_3.cisa_representative_email = "test@igorville.com"
            cls.domain_request_3.last_submitted_date = get_time_aware_date(datetime(2024, 4, 2))
            cls.domain_request_3.save()

            cls.domain_request_4.last_submitted_date = get_time_aware_date(datetime(2024, 4, 2))
            cls.domain_request_4.save()

            cls.domain_request_6.last_submitted_date = get_time_aware_date(datetime(2024, 4, 2))
            cls.domain_request_6.save()

    @classmethod
    def sharedTearDown(cls):
        PublicContact.objects.all().delete()
        Domain.objects.all().delete()
        DomainInformation.objects.all().delete()
        DomainRequest.objects.all().delete()
        UserDomainRole.objects.all().delete()
        Suborganization.objects.all().delete()
        Portfolio.objects.all().delete()
        UserPortfolioPermission.objects.all().delete()
        User.objects.all().delete()
        DomainInvitation.objects.all().delete()
        PortfolioInvitation.objects.all().delete()
        cls.federal_agency_1.delete()
        cls.federal_agency_2.delete()


class MockDbForSharedTests(MockDb):
    """Set up and tear down test data that is shared across all tests in a class"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sharedSetUp()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.sharedTearDown()


class MockDbForIndividualTests(MockDb):
    """Set up and tear down test data for each test in a class"""

    def setUp(self):
        super().setUp()
        self.sharedSetUp()

    def tearDown(self):
        super().tearDown()
        self.sharedTearDown()


def mock_user():
    """A simple user."""
    user_kwargs = dict(
        first_name="Jeff",
        last_name="Lebowski",
    )
    mock_user, _ = User.objects.get_or_create(**user_kwargs)
    return mock_user


def create_superuser(**kwargs):
    """Creates a analyst user with is_staff=True and the group full_access_group"""
    User = get_user_model()
    p = "adminpass"
    user = User.objects.create_user(
        username=kwargs.get("username", "superuser"),
        email=kwargs.get("email", "admin@example.com"),
        first_name=kwargs.get("first_name", "first"),
        last_name=kwargs.get("last_name", "last"),
        is_staff=kwargs.get("is_staff", True),
        password=kwargs.get("password", p),
        phone=kwargs.get("phone", "8003111234"),
    )
    # Retrieve the group or create it if it doesn't exist
    group, _ = UserGroup.objects.get_or_create(name="full_access_group")
    # Add the user to the group
    user.groups.set([group])
    return user


def create_user(**kwargs):
    """Creates a analyst user with is_staff=True and the group cisa_analysts_group"""
    User = get_user_model()
    p = "userpass"
    user = User.objects.create_user(
        username=kwargs.get("username", "staffuser"),
        email=kwargs.get("email", "staff@example.com"),
        first_name=kwargs.get("first_name", "first"),
        last_name=kwargs.get("last_name", "last"),
        is_staff=kwargs.get("is_staff", True),
        title=kwargs.get("title", "title"),
        password=kwargs.get("password", p),
        phone=kwargs.get("phone", "8003111234"),
    )
    # Retrieve the group or create it if it doesn't exist
    group, _ = UserGroup.objects.get_or_create(name="cisa_analysts_group")
    # Add the user to the group
    user.groups.set([group])
    return user


def create_test_user():
    username = "test_user"
    first_name = "First"
    last_name = "Last"
    email = "info@example.com"
    phone = "8003111234"
    title = "test title"
    user = get_user_model().objects.create(
        username=username,
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone=phone,
        title=title,
    )
    return user


def create_ready_domain():
    domain, _ = Domain.objects.get_or_create(name="city.gov", state=Domain.State.READY)
    return domain


# TODO in 1793: Remove the federal agency/updated federal agency fields
def completed_domain_request(  # noqa
    has_other_contacts=True,
    # pass empty [] if you want current_websites or alternative_domains set to None
    current_websites=["city.com"],
    alternative_domains=["city1.gov"],
    has_about_your_organization=True,
    has_anything_else=True,
    has_cisa_representative=True,
    status=DomainRequest.DomainRequestStatus.STARTED,
    user=False,
    name="city.gov",
    investigator=None,
    generic_org_type="federal",
    is_election_board=False,
    organization_type=None,
    federal_agency=None,
    federal_type=None,
    action_needed_reason=None,
    city=None,
    state_territory=None,
    portfolio=None,
    organization_name=None,
    sub_organization=None,
    requested_suborganization=None,
    suborganization_city=None,
    suborganization_state_territory=None,
):
    """A completed domain request."""
    if not user:
        user = get_user_model().objects.create(username="username" + str(uuid.uuid4())[:8])
    so, _ = Contact.objects.get_or_create(
        first_name="Testy",
        last_name="Tester",
        title="Chief Tester",
        email="testy@town.com",
        phone="(555) 555 5555",
    )
    domain, _ = DraftDomain.objects.get_or_create(name=name)
    other, _ = Contact.objects.get_or_create(
        first_name="Testy",
        last_name="Tester",
        title="Another Tester",
        email="testy2@town.com",
        phone="(555) 555 5557",
    )
    if not investigator:
        investigator, _ = User.objects.get_or_create(
            username="incrediblyfakeinvestigator" + str(uuid.uuid4())[:8],
            first_name="Joe",
            last_name="Bob",
            is_staff=True,
        )

    domain_request_kwargs = dict(
        generic_org_type=generic_org_type,
        is_election_board=is_election_board,
        federal_type="executive",
        purpose="Purpose of the site",
        is_policy_acknowledged=True,
        organization_name=organization_name if organization_name else "Testorg",
        address_line1="address 1",
        address_line2="address 2",
        state_territory="NY" if not state_territory else state_territory,
        zipcode="10002",
        senior_official=so,
        requested_domain=domain,
        creator=user,
        status=status,
        investigator=investigator,
        federal_agency=federal_agency,
    )

    if city:
        domain_request_kwargs["city"] = city

    if has_about_your_organization:
        domain_request_kwargs["about_your_organization"] = "e-Government"
    if has_anything_else:
        domain_request_kwargs["anything_else"] = "There is more"

    if organization_type:
        domain_request_kwargs["organization_type"] = organization_type

    if federal_type:
        domain_request_kwargs["federal_type"] = federal_type

    if action_needed_reason:
        domain_request_kwargs["action_needed_reason"] = action_needed_reason

    if portfolio:
        domain_request_kwargs["portfolio"] = portfolio

    if sub_organization:
        domain_request_kwargs["sub_organization"] = sub_organization

    if requested_suborganization:
        domain_request_kwargs["requested_suborganization"] = requested_suborganization

    if suborganization_city:
        domain_request_kwargs["suborganization_city"] = suborganization_city

    if suborganization_state_territory:
        domain_request_kwargs["suborganization_state_territory"] = suborganization_state_territory

    domain_request, _ = DomainRequest.objects.get_or_create(**domain_request_kwargs)

    if has_other_contacts:
        domain_request.other_contacts.add(other)
    if len(current_websites) > 0:
        for website in current_websites:
            current, _ = Website.objects.get_or_create(website=website)
            domain_request.current_websites.add(current)
    if len(alternative_domains) > 0:
        for alternative_domain in alternative_domains:
            alt, _ = Website.objects.get_or_create(website=alternative_domain)
            domain_request.alternative_domains.add(alt)
    if has_cisa_representative:
        domain_request.cisa_representative_first_name = "CISA-first-name"
        domain_request.cisa_representative_last_name = "CISA-last-name"
        domain_request.cisa_representative_email = "cisaRep@igorville.gov"

    return domain_request


def set_domain_request_investigators(domain_request_list: list[DomainRequest], investigator_user: User):
    """Helper method that sets the investigator field of all provided domain requests"""
    for request in domain_request_list:
        request.investigator = investigator_user
        request.save()


def multiple_unalphabetical_domain_objects(
    domain_type=AuditedAdminMockData.DOMAIN_REQUEST,
):
    """Returns a list of generic domain objects for testing purposes"""
    domain_requests = []
    list_of_letters = list(ascii_uppercase)
    random.shuffle(list_of_letters)

    mock = AuditedAdminMockData()
    for object_name in list_of_letters:
        domain_request = mock.create_full_dummy_domain_object(domain_type, object_name)
        domain_requests.append(domain_request)
    return domain_requests


def generic_domain_object(domain_type, object_name):
    """Returns a generic domain object of
    domain_type 'domain_request', 'information', or 'invitation'"""
    mock = AuditedAdminMockData()
    domain_request = mock.create_full_dummy_domain_object(domain_type, object_name)
    return domain_request


class MockEppLib(TestCase):
    class fakedEppObject(object):
        """"""

        def __init__(
            self,
            auth_info=...,
            cr_date=...,
            contacts=...,
            hosts=...,
            statuses=...,
            avail=...,
            addrs=...,
            registrant=...,
            ex_date=...,
        ):
            self.auth_info = auth_info
            self.cr_date = cr_date
            self.contacts = contacts
            self.hosts = hosts
            self.statuses = statuses
            self.avail = avail  # use for CheckDomain
            self.addrs = addrs
            self.registrant = registrant
            self.ex_date = ex_date

        def dummyInfoContactResultData(
            self,
            id,
            email,
            cr_date=make_aware(datetime(2023, 5, 25, 19, 45, 35)),
            pw="thisisnotapassword",
        ):
            fake = info.InfoContactResultData(
                id=id,
                postal_info=common.PostalInfo(
                    name="Registry Customer Service",
                    addr=common.ContactAddr(
                        street=["4200 Wilson Blvd."],
                        city="Arlington",
                        pc="22201",
                        cc="US",
                        sp="VA",
                    ),
                    org="Cybersecurity and Infrastructure Security Agency",
                    type="type",
                ),
                voice="+1.8882820870",
                fax="+1-212-9876543",
                email=email,
                auth_info=common.ContactAuthInfo(pw=pw),
                roid=...,
                statuses=[],
                cl_id=...,
                cr_id=...,
                cr_date=cr_date,
                up_id=...,
                up_date=...,
                tr_date=...,
                disclose=...,
                vat=...,
                ident=...,
                notify_email=...,
            )
            return fake

    mockDataInfoDomain = fakedEppObject(
        "fakePw",
        cr_date=make_aware(datetime(2023, 5, 25, 19, 45, 35)),
        contacts=[
            common.DomainContact(
                contact="securityContact",
                type=PublicContact.ContactTypeChoices.SECURITY,
            ),
            common.DomainContact(
                contact="technicalContact",
                type=PublicContact.ContactTypeChoices.TECHNICAL,
            ),
            common.DomainContact(
                contact="adminContact",
                type=PublicContact.ContactTypeChoices.ADMINISTRATIVE,
            ),
        ],
        hosts=["fake.host.com"],
        statuses=[
            common.Status(state="serverTransferProhibited", description="", lang="en"),
            common.Status(state="inactive", description="", lang="en"),
        ],
        registrant="regContact",
        ex_date=date(2023, 5, 25),
    )

    mockDataInfoDomainSubdomain = fakedEppObject(
        "fakePw",
        cr_date=make_aware(datetime(2023, 5, 25, 19, 45, 35)),
        contacts=[common.DomainContact(contact="123", type=PublicContact.ContactTypeChoices.SECURITY)],
        hosts=["fake.meoward.gov"],
        statuses=[
            common.Status(state="serverTransferProhibited", description="", lang="en"),
            common.Status(state="inactive", description="", lang="en"),
        ],
        ex_date=date(2023, 5, 25),
    )

    mockDataInfoDomainSubdomainAndIPAddress = fakedEppObject(
        "fakePw",
        cr_date=make_aware(datetime(2023, 5, 25, 19, 45, 35)),
        contacts=[common.DomainContact(contact="123", type=PublicContact.ContactTypeChoices.SECURITY)],
        hosts=["fake.meow.gov"],
        statuses=[
            common.Status(state="serverTransferProhibited", description="", lang="en"),
            common.Status(state="inactive", description="", lang="en"),
        ],
        ex_date=date(2023, 5, 25),
        addrs=[common.Ip(addr="2.0.0.8")],
    )

    mockDataInfoDomainNotSubdomainNoIP = fakedEppObject(
        "fakePw",
        cr_date=make_aware(datetime(2023, 5, 25, 19, 45, 35)),
        contacts=[common.DomainContact(contact="123", type=PublicContact.ContactTypeChoices.SECURITY)],
        hosts=["fake.meow.com"],
        statuses=[
            common.Status(state="serverTransferProhibited", description="", lang="en"),
            common.Status(state="inactive", description="", lang="en"),
        ],
        ex_date=date(2023, 5, 25),
    )

    mockDataInfoDomainSubdomainNoIP = fakedEppObject(
        "fakePw",
        cr_date=make_aware(datetime(2023, 5, 25, 19, 45, 35)),
        contacts=[common.DomainContact(contact="123", type=PublicContact.ContactTypeChoices.SECURITY)],
        hosts=["fake.subdomainwoip.gov"],
        statuses=[
            common.Status(state="serverTransferProhibited", description="", lang="en"),
            common.Status(state="inactive", description="", lang="en"),
        ],
        ex_date=date(2023, 5, 25),
    )

    mockDataExtensionDomain = fakedEppObject(
        "fakePw",
        cr_date=make_aware(datetime(2023, 5, 25, 19, 45, 35)),
        contacts=[common.DomainContact(contact="123", type=PublicContact.ContactTypeChoices.SECURITY)],
        hosts=["fake.host.com"],
        statuses=[
            common.Status(state="serverTransferProhibited", description="", lang="en"),
            common.Status(state="inactive", description="", lang="en"),
        ],
        ex_date=date(2023, 11, 15),
    )
    mockDataInfoContact = mockDataInfoDomain.dummyInfoContactResultData(
        id="123", email="123@mail.gov", cr_date=datetime(2023, 5, 25, 19, 45, 35), pw="lastPw"
    )
    mockDataSecurityContact = mockDataInfoDomain.dummyInfoContactResultData(
        id="securityContact", email="security@mail.gov", cr_date=datetime(2023, 5, 25, 19, 45, 35), pw="lastPw"
    )
    InfoDomainWithContacts = fakedEppObject(
        "fakePw",
        cr_date=make_aware(datetime(2023, 5, 25, 19, 45, 35)),
        contacts=[
            common.DomainContact(
                contact="securityContact",
                type=PublicContact.ContactTypeChoices.SECURITY,
            ),
            common.DomainContact(
                contact="technicalContact",
                type=PublicContact.ContactTypeChoices.TECHNICAL,
            ),
            common.DomainContact(
                contact="adminContact",
                type=PublicContact.ContactTypeChoices.ADMINISTRATIVE,
            ),
        ],
        hosts=["fake.host.com"],
        statuses=[
            common.Status(state="serverTransferProhibited", description="", lang="en"),
            common.Status(state="inactive", description="", lang="en"),
        ],
        registrant="regContact",
        ex_date=date(2023, 11, 15),
    )

    InfoDomainWithDefaultSecurityContact = fakedEppObject(
        "fakepw",
        cr_date=make_aware(datetime(2023, 5, 25, 19, 45, 35)),
        contacts=[
            common.DomainContact(
                contact="defaultSec",
                type=PublicContact.ContactTypeChoices.SECURITY,
            )
        ],
        hosts=["fake.host.com"],
        statuses=[
            common.Status(state="serverTransferProhibited", description="", lang="en"),
            common.Status(state="inactive", description="", lang="en"),
        ],
    )

    mockVerisignDataInfoContact = mockDataInfoDomain.dummyInfoContactResultData(
        "defaultVeri", "registrar@dotgov.gov", datetime(2023, 5, 25, 19, 45, 35), "lastPw"
    )
    InfoDomainWithVerisignSecurityContact = fakedEppObject(
        "fakepw",
        cr_date=make_aware(datetime(2023, 5, 25, 19, 45, 35)),
        contacts=[
            common.DomainContact(
                contact="defaultVeri",
                type=PublicContact.ContactTypeChoices.SECURITY,
            )
        ],
        hosts=["fake.host.com"],
        statuses=[
            common.Status(state="serverTransferProhibited", description="", lang="en"),
            common.Status(state="inactive", description="", lang="en"),
        ],
    )

    InfoDomainWithDefaultTechnicalContact = fakedEppObject(
        "fakepw",
        cr_date=make_aware(datetime(2023, 5, 25, 19, 45, 35)),
        contacts=[
            common.DomainContact(
                contact="defaultTech",
                type=PublicContact.ContactTypeChoices.TECHNICAL,
            )
        ],
        hosts=["fake.host.com"],
        statuses=[
            common.Status(state="serverTransferProhibited", description="", lang="en"),
            common.Status(state="inactive", description="", lang="en"),
        ],
    )

    mockDefaultTechnicalContact = InfoDomainWithContacts.dummyInfoContactResultData(
        "defaultTech", "dotgov@cisa.dhs.gov"
    )
    mockDefaultSecurityContact = InfoDomainWithContacts.dummyInfoContactResultData("defaultSec", "dotgov@cisa.dhs.gov")
    mockSecurityContact = InfoDomainWithContacts.dummyInfoContactResultData("securityContact", "security@mail.gov")
    mockTechnicalContact = InfoDomainWithContacts.dummyInfoContactResultData("technicalContact", "tech@mail.gov")
    mockAdministrativeContact = InfoDomainWithContacts.dummyInfoContactResultData("adminContact", "admin@mail.gov")
    mockRegistrantContact = InfoDomainWithContacts.dummyInfoContactResultData("regContact", "registrant@mail.gov")

    infoDomainNoContact = fakedEppObject(
        "security",
        cr_date=make_aware(datetime(2023, 5, 25, 19, 45, 35)),
        contacts=[],
        hosts=["fake.host.com"],
    )

    infoDomainSharedHost = fakedEppObject(
        "sharedHost.gov",
        cr_date=make_aware(datetime(2023, 5, 25, 19, 45, 35)),
        contacts=[],
        hosts=[
            "ns1.sharedhost.com",
        ],
    )

    infoDomainThreeHosts = fakedEppObject(
        "my-nameserver.gov",
        cr_date=make_aware(datetime(2023, 5, 25, 19, 45, 35)),
        contacts=[],
        hosts=[
            "ns1.my-nameserver-1.com",
            "ns1.my-nameserver-2.com",
            "ns1.cats-are-superior3.com",
        ],
    )

    infoDomainFourHosts = fakedEppObject(
        "fournameserversDomain.gov",
        cr_date=make_aware(datetime(2023, 5, 25, 19, 45, 35)),
        contacts=[],
        hosts=[
            "ns1.my-nameserver-1.com",
            "ns1.my-nameserver-2.com",
            "ns1.cats-are-superior3.com",
            "ns1.explosive-chicken-nuggets.com",
        ],
    )

    infoDomainNoHost = fakedEppObject(
        "my-nameserver.gov",
        cr_date=make_aware(datetime(2023, 5, 25, 19, 45, 35)),
        contacts=[],
        hosts=[],
    )

    infoDomainTwoHosts = fakedEppObject(
        "my-nameserver.gov",
        cr_date=make_aware(datetime(2023, 5, 25, 19, 45, 35)),
        contacts=[],
        hosts=["ns1.my-nameserver-1.com", "ns1.my-nameserver-2.com"],
    )

    mockDataInfoHosts = fakedEppObject(
        "lastPw",
        cr_date=make_aware(datetime(2023, 8, 25, 19, 45, 35)),
        addrs=[common.Ip(addr="1.2.3.4"), common.Ip(addr="2.3.4.5")],
    )

    mockDataInfoHosts1IP = fakedEppObject(
        "lastPw",
        cr_date=make_aware(datetime(2023, 8, 25, 19, 45, 35)),
        addrs=[common.Ip(addr="2.0.0.8")],
    )

    mockDataInfoHostsNotSubdomainNoIP = fakedEppObject(
        "lastPw",
        cr_date=make_aware(datetime(2023, 8, 26, 19, 45, 35)),
        addrs=[],
    )

    mockDataInfoHostsSubdomainNoIP = fakedEppObject(
        "lastPw",
        cr_date=make_aware(datetime(2023, 8, 27, 19, 45, 35)),
        addrs=[],
    )

    mockDataHostChange = fakedEppObject("lastPw", cr_date=make_aware(datetime(2023, 8, 25, 19, 45, 35)))
    addDsData1 = {
        "keyTag": 1234,
        "alg": 3,
        "digestType": 1,
        "digest": "ec0bdd990b39feead889f0ba613db4adec0bdd99",
    }
    addDsData2 = {
        "keyTag": 2345,
        "alg": 3,
        "digestType": 1,
        "digest": "ec0bdd990b39feead889f0ba613db4adecb4adec",
    }
    dnssecExtensionWithDsData = extensions.DNSSECExtension(
        **{
            "dsData": [
                common.DSData(**addDsData1)  # type: ignore
            ],  # type: ignore
        }
    )
    dnssecExtensionWithMultDsData = extensions.DNSSECExtension(
        **{
            "dsData": [
                common.DSData(**addDsData1),  # type: ignore
                common.DSData(**addDsData2),  # type: ignore
            ],  # type: ignore
        }
    )
    dnssecExtensionRemovingDsData = extensions.DNSSECExtension()

    infoDomainHasIP = fakedEppObject(
        "nameserverwithip.gov",
        cr_date=make_aware(datetime(2023, 5, 25, 19, 45, 35)),
        contacts=[
            common.DomainContact(
                contact="securityContact",
                type=PublicContact.ContactTypeChoices.SECURITY,
            ),
            common.DomainContact(
                contact="technicalContact",
                type=PublicContact.ContactTypeChoices.TECHNICAL,
            ),
            common.DomainContact(
                contact="adminContact",
                type=PublicContact.ContactTypeChoices.ADMINISTRATIVE,
            ),
        ],
        hosts=[
            "ns1.nameserverwithip.gov",
            "ns2.nameserverwithip.gov",
            "ns3.nameserverwithip.gov",
        ],
        addrs=[common.Ip(addr="1.2.3.4"), common.Ip(addr="2.3.4.5")],
    )

    justNameserver = fakedEppObject(
        "justnameserver.com",
        cr_date=make_aware(datetime(2023, 5, 25, 19, 45, 35)),
        contacts=[
            common.DomainContact(
                contact="securityContact",
                type=PublicContact.ContactTypeChoices.SECURITY,
            ),
            common.DomainContact(
                contact="technicalContact",
                type=PublicContact.ContactTypeChoices.TECHNICAL,
            ),
            common.DomainContact(
                contact="adminContact",
                type=PublicContact.ContactTypeChoices.ADMINISTRATIVE,
            ),
        ],
        hosts=[
            "ns1.justnameserver.com",
            "ns2.justnameserver.com",
        ],
    )

    infoDomainCheckHostIPCombo = fakedEppObject(
        "nameserversubdomain.gov",
        cr_date=make_aware(datetime(2023, 5, 25, 19, 45, 35)),
        contacts=[],
        hosts=[
            "ns1.nameserversubdomain.gov",
            "ns2.nameserversubdomain.gov",
        ],
    )

    mockRenewedDomainExpDate = fakedEppObject(
        "fake.gov",
        ex_date=date(2023, 5, 25),
    )

    mockButtonRenewedDomainExpDate = fakedEppObject(
        "fake.gov",
        ex_date=date(2025, 5, 25),
    )

    mockDnsNeededRenewedDomainExpDate = fakedEppObject(
        "fakeneeded.gov",
        ex_date=date(2023, 2, 15),
    )

    mockMaximumRenewedDomainExpDate = fakedEppObject(
        "fakemaximum.gov",
        ex_date=date(2024, 12, 31),
    )

    mockRecentRenewedDomainExpDate = fakedEppObject(
        "waterbutpurple.gov",
        ex_date=date(2024, 11, 15),
    )

    def _mockDomainName(self, _name, _avail=False):
        return MagicMock(
            res_data=[
                responses.check.CheckDomainResultData(name=_name, avail=_avail, reason=None),
            ]
        )

    def mockCheckDomainCommand(self, _request, cleaned):
        if "gsa.gov" in getattr(_request, "names", None):
            return self._mockDomainName("gsa.gov", False)
        elif "igorville.gov" in getattr(_request, "names", None):
            return self._mockDomainName("igorville.gov", True)
        elif "top-level-agency.gov" in getattr(_request, "names", None):
            return self._mockDomainName("top-level-agency.gov", True)
        elif "city.gov" in getattr(_request, "names", None):
            return self._mockDomainName("city.gov", True)
        elif "city1.gov" in getattr(_request, "names", None):
            return self._mockDomainName("city1.gov", True)
        elif "errordomain.gov" in getattr(_request, "names", None):
            raise RegistryError("Registry cannot find domain availability.")
        else:
            return self._mockDomainName("domainnotfound.gov", False)

    def mockSend(self, _request, cleaned):
        """Mocks the registry.send function used inside of domain.py
        registry is imported from epplibwrapper
        returns objects that simulate what would be in a epp response
        but only relevant pieces for tests"""

        match type(_request):
            case commands.InfoDomain:
                return self.mockInfoDomainCommands(_request, cleaned)
            case commands.InfoContact:
                return self.mockInfoContactCommands(_request, cleaned)
            case commands.CreateContact:
                return self.mockCreateContactCommands(_request, cleaned)
            case commands.DeleteContact:
                return self.mockDeleteContactCommands(_request, cleaned)
            case commands.UpdateDomain:
                return self.mockUpdateDomainCommands(_request, cleaned)
            case commands.CreateHost:
                return self.mockCreateHostCommands(_request, cleaned)
            case commands.UpdateHost:
                return self.mockUpdateHostCommands(_request, cleaned)
            case commands.DeleteHost:
                return self.mockDeleteHostCommands(_request, cleaned)
            case commands.CheckDomain:
                return self.mockCheckDomainCommand(_request, cleaned)
            case commands.DeleteDomain:
                return self.mockDeleteDomainCommands(_request, cleaned)
            case commands.RenewDomain:
                return self.mockRenewDomainCommand(_request, cleaned)
            case commands.InfoHost:
                return self.mockInfoHostCommmands(_request, cleaned)
            case _:
                return MagicMock(res_data=[self.mockDataInfoHosts])

    def mockCreateHostCommands(self, _request, cleaned):
        test_ws_ip = common.Ip(addr="1.1. 1.1")
        addrs_submitted = getattr(_request, "addrs", [])
        if test_ws_ip in addrs_submitted:
            raise RegistryError(code=ErrorCode.PARAMETER_VALUE_RANGE_ERROR)
        else:
            return MagicMock(
                res_data=[self.mockDataHostChange],
                code=ErrorCode.COMMAND_COMPLETED_SUCCESSFULLY,
            )

    def mockInfoHostCommmands(self, _request, cleaned):
        request_name = getattr(_request, "name", None)

        # Define a dictionary to map request names to data and extension values
        request_mappings = {
            "fake.meow.gov": (self.mockDataInfoHosts1IP, None),  # is subdomain and has ip
            "fake.meow.com": (self.mockDataInfoHostsNotSubdomainNoIP, None),  # not subdomain w no ip
            "fake.subdomainwoip.gov": (self.mockDataInfoHostsSubdomainNoIP, None),  # subdomain w no ip
        }

        # Retrieve the corresponding values from the dictionary
        default_mapping = (self.mockDataInfoHosts, None)
        res_data, extensions = request_mappings.get(request_name, default_mapping)

        return MagicMock(
            res_data=[res_data],
            extensions=[extensions] if extensions is not None else [],
        )

    def mockUpdateHostCommands(self, _request, cleaned):
        test_ws_ip = common.Ip(addr="1.1. 1.1")
        addrs_submitted = getattr(_request, "addrs", [])
        if test_ws_ip in addrs_submitted:
            raise RegistryError(code=ErrorCode.PARAMETER_VALUE_RANGE_ERROR)
        else:
            return MagicMock(
                res_data=[self.mockDataHostChange],
                code=ErrorCode.COMMAND_COMPLETED_SUCCESSFULLY,
            )

    def mockDeleteHostCommands(self, _request, cleaned):
        host = getattr(_request, "name", None)
        if "sharedhost.com" in host:
            raise RegistryError(code=ErrorCode.OBJECT_ASSOCIATION_PROHIBITS_OPERATION, note="ns1.sharedhost.com")
        return MagicMock(
            res_data=[self.mockDataHostChange],
            code=ErrorCode.COMMAND_COMPLETED_SUCCESSFULLY,
        )

    def mockUpdateDomainCommands(self, _request, cleaned):
        if getattr(_request, "name", None) == "dnssec-invalid.gov":
            raise RegistryError(code=ErrorCode.PARAMETER_VALUE_RANGE_ERROR)
        else:
            return MagicMock(
                res_data=[self.mockDataHostChange],
                code=ErrorCode.COMMAND_COMPLETED_SUCCESSFULLY,
            )

    def mockDeleteDomainCommands(self, _request, cleaned):
        if getattr(_request, "name", None) == "failDelete.gov":
            raise RegistryError(code=ErrorCode.OBJECT_ASSOCIATION_PROHIBITS_OPERATION)
        return None

    def mockRenewDomainCommand(self, _request, cleaned):
        if getattr(_request, "name", None) == "fake-error.gov":
            raise RegistryError(code=ErrorCode.PARAMETER_VALUE_RANGE_ERROR)
        elif getattr(_request, "name", None) == "waterbutpurple.gov":
            return MagicMock(
                res_data=[self.mockRecentRenewedDomainExpDate],
                code=ErrorCode.COMMAND_COMPLETED_SUCCESSFULLY,
            )
        elif getattr(_request, "name", None) == "fakeneeded.gov":
            return MagicMock(
                res_data=[self.mockDnsNeededRenewedDomainExpDate],
                code=ErrorCode.COMMAND_COMPLETED_SUCCESSFULLY,
            )
        elif getattr(_request, "name", None) == "fakemaximum.gov":
            return MagicMock(
                res_data=[self.mockMaximumRenewedDomainExpDate],
                code=ErrorCode.COMMAND_COMPLETED_SUCCESSFULLY,
            )
        elif getattr(_request, "name", None) == "fake.gov":
            period = getattr(_request, "period", None)
            extension_period = getattr(period, "length", None)
            if extension_period == 2:
                return MagicMock(
                    res_data=[self.mockButtonRenewedDomainExpDate],
                    code=ErrorCode.COMMAND_COMPLETED_SUCCESSFULLY,
                )
            else:
                return MagicMock(
                    res_data=[self.mockRenewedDomainExpDate],
                    code=ErrorCode.COMMAND_COMPLETED_SUCCESSFULLY,
                )

    def mockInfoDomainCommands(self, _request, cleaned):
        request_name = getattr(_request, "name", None).lower()

        # Define a dictionary to map request names to data and extension values
        request_mappings = {
            "fake.gov": (self.mockDataInfoDomain, None),
            "security.gov": (self.infoDomainNoContact, None),
            "dnssec-dsdata.gov": (
                self.mockDataInfoDomain,
                self.dnssecExtensionWithDsData,
            ),
            "dnssec-multdsdata.gov": (
                self.mockDataInfoDomain,
                self.dnssecExtensionWithMultDsData,
            ),
            "dnssec-none.gov": (self.mockDataInfoDomain, None),
            "my-nameserver.gov": (
                self.infoDomainTwoHosts if self.mockedSendFunction.call_count == 5 else self.infoDomainNoHost,
                None,
            ),
            "waterbutpurple.gov": (self.mockDataExtensionDomain, None),
            "nameserverwithip.gov": (self.infoDomainHasIP, None),
            "namerserversubdomain.gov": (self.infoDomainCheckHostIPCombo, None),
            "freeman.gov": (self.InfoDomainWithContacts, None),
            "threenameserversdomain.gov": (self.infoDomainThreeHosts, None),
            "fournameserversdomain.gov": (self.infoDomainFourHosts, None),
            "defaultsecurity.gov": (self.InfoDomainWithDefaultSecurityContact, None),
            "adomain2.gov": (self.InfoDomainWithVerisignSecurityContact, None),
            "defaulttechnical.gov": (self.InfoDomainWithDefaultTechnicalContact, None),
            "justnameserver.com": (self.justNameserver, None),
            "meoward.gov": (self.mockDataInfoDomainSubdomain, None),
            "meow.gov": (self.mockDataInfoDomainSubdomainAndIPAddress, None),
            "fakemeow.gov": (self.mockDataInfoDomainNotSubdomainNoIP, None),
            "subdomainwoip.gov": (self.mockDataInfoDomainSubdomainNoIP, None),
            "ddomain3.gov": (self.InfoDomainWithContacts, None),
            "igorville.gov": (self.InfoDomainWithContacts, None),
            "sharingiscaring.gov": (self.infoDomainSharedHost, None),
        }

        # Retrieve the corresponding values from the dictionary
        res_data, extensions = request_mappings.get(request_name, (self.mockDataInfoDomain, None))

        return MagicMock(
            res_data=[res_data],
            extensions=[extensions] if extensions is not None else [],
        )

    def mockInfoContactCommands(self, _request, cleaned):
        mocked_result: info.InfoContactResultData

        # For testing contact types
        match getattr(_request, "id", None):
            case "securityContact":
                mocked_result = self.mockSecurityContact
            case "technicalContact":
                mocked_result = self.mockTechnicalContact
            case "adminContact":
                mocked_result = self.mockAdministrativeContact
            case "regContact":
                mocked_result = self.mockRegistrantContact
            case "defaultSec":
                mocked_result = self.mockDefaultSecurityContact
            case "defaultTech":
                mocked_result = self.mockDefaultTechnicalContact
            case "defaultVeri":
                mocked_result = self.mockVerisignDataInfoContact
            case _:
                # Default contact return
                mocked_result = self.mockDataInfoContact

        return MagicMock(res_data=[mocked_result])

    def mockCreateContactCommands(self, _request, cleaned):
        if getattr(_request, "id", None) == "fail" and self.mockedSendFunction.call_count == 3:
            # use this for when a contact is being updated
            # sets the second send() to fail
            raise RegistryError(code=ErrorCode.OBJECT_EXISTS)
        elif getattr(_request, "email", None) == "test@failCreate.gov":
            # use this for when a contact is being updated
            # mocks a registry error on creation
            raise RegistryError(code=None)
        elif getattr(_request, "email", None) == "test@contactError.gov":
            # use this for when a contact is being updated
            # mocks a contact error on creation
            raise ContactError(code=ContactErrorCodes.CONTACT_TYPE_NONE)
        return MagicMock(res_data=[self.mockDataInfoHosts])

    def mockDeleteContactCommands(self, _request, cleaned):
        if getattr(_request, "id", None) == "fail":
            raise RegistryError(code=ErrorCode.OBJECT_EXISTS)
        else:
            return MagicMock(
                res_data=[self.mockDataInfoContact],
                code=ErrorCode.COMMAND_COMPLETED_SUCCESSFULLY,
            )

    def setUp(self):
        """mock epp send function as this will fail locally"""
        self.mockSendPatch = patch("registrar.models.domain.registry.send")
        self.mockedSendFunction = self.mockSendPatch.start()
        self.mockedSendFunction.side_effect = self.mockSend

    def _convertPublicContactToEpp(self, contact: PublicContact, disclose_email=False, createContact=True):
        DF = common.DiscloseField
        fields = {DF.EMAIL}

        di = common.Disclose(
            flag=disclose_email,
            fields=fields,
        )

        # check docs here looks like we may have more than one address field but
        addr = common.ContactAddr(
            [
                getattr(contact, street) for street in ["street1", "street2", "street3"] if hasattr(contact, street)
            ],  # type: ignore
            city=contact.city,
            pc=contact.pc,
            cc=contact.cc,
            sp=contact.sp,
        )  # type: ignore

        pi = common.PostalInfo(
            name=contact.name,
            addr=addr,
            org=contact.org,
            type="loc",
        )

        ai = common.ContactAuthInfo(pw="2fooBAR123fooBaz")
        if createContact:
            return commands.CreateContact(
                id=contact.registry_id,
                postal_info=pi,  # type: ignore
                email=contact.email,
                voice=contact.voice,
                fax=contact.fax,
                auth_info=ai,
                disclose=di,
                vat=None,
                ident=None,
                notify_email=None,
            )  # type: ignore
        else:
            return commands.UpdateContact(
                id=contact.registry_id,
                postal_info=pi,
                email=contact.email,
                voice=contact.voice,
                fax=contact.fax,
                disclose=di,
                auth_info=ai,
            )

    def tearDown(self):
        self.mockSendPatch.stop()


def get_wsgi_request_object(client, user, url="/"):
    """Returns client.get(url).wsgi_request for testing functions or classes
    that need a request object directly passed to them."""
    client.force_login(user)
    request = client.get(url).wsgi_request
    request.user = user
    return request
