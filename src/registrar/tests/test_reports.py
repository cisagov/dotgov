import io
from django.test import Client, RequestFactory
from io import StringIO
from registrar.models import (
    DomainRequest,
    Domain,
    UserDomainRole,
    PortfolioInvitation,
    User,
)
from registrar.models import Portfolio, DraftDomain
from registrar.models.user_portfolio_permission import UserPortfolioPermission
from registrar.models.utility.portfolio_helper import UserPortfolioRoleChoices
from registrar.utility.csv_export import (
    DomainDataFull,
    DomainDataType,
    DomainDataFederal,
    DomainDataTypeUser,
    DomainRequestDataType,
    DomainGrowth,
    DomainManaged,
    DomainUnmanaged,
    DomainExport,
    DomainRequestExport,
    DomainRequestGrowth,
    DomainRequestDataFull,
    MemberExport,
    get_default_start_date,
    get_default_end_date,
)
from django.db.models import Case, When
from django.core.management import call_command
from unittest.mock import MagicMock, call, mock_open, patch
from api.views import get_current_federal, get_current_full
from django.conf import settings
from botocore.exceptions import ClientError
import boto3_mocking
from registrar.utility.s3_bucket import S3ClientError, S3ClientErrorCodes  # type: ignore
from django.utils import timezone
from api.tests.common import less_console_noise_decorator
from .common import (
    MockDbForSharedTests,
    MockDbForIndividualTests,
    MockEppLib,
    get_wsgi_request_object,
    less_console_noise,
    get_time_aware_date,
    GenericTestHelper,
)
from waffle.testutils import override_flag

from datetime import datetime
from django.contrib.admin.models import LogEntry, ADDITION
from django.contrib.contenttypes.models import ContentType


class CsvReportsTest(MockDbForSharedTests):
    """Tests to determine if we are uploading our reports correctly."""

    def setUp(self):
        """setup fake comain data"""
        super().setUp()
        self.client = Client(HTTP_HOST="localhost:8080")
        self.factory = RequestFactory()

    @boto3_mocking.patching
    def test_generate_federal_report(self):
        """Ensures that we correctly generate current-federal.csv"""
        with less_console_noise():
            mock_client = MagicMock()
            fake_open = mock_open()
            expected_file_content = [
                call("Domain name,Domain type,Agency,Organization name,City,State,Security contact email\r\n"),
                call("cdomain11.gov,Federal - Executive,World War I Centennial Commission,,,,(blank)\r\n"),
                call("cdomain1.gov,Federal - Executive,World War I Centennial Commission,,,,(blank)\r\n"),
                call("adomain10.gov,Federal,Armed Forces Retirement Home,,,,(blank)\r\n"),
                call("ddomain3.gov,Federal,Armed Forces Retirement Home,,,,(blank)\r\n"),
            ]
            # We don't actually want to write anything for a test case,
            # we just want to verify what is being written.
            with boto3_mocking.clients.handler_for("s3", mock_client):
                with patch("builtins.open", fake_open):
                    call_command("generate_current_federal_report", checkpath=False)
            content = fake_open()

            content.write.assert_has_calls(expected_file_content)

    @boto3_mocking.patching
    def test_generate_full_report(self):
        """Ensures that we correctly generate current-full.csv"""
        with less_console_noise():
            mock_client = MagicMock()
            fake_open = mock_open()
            expected_file_content = [
                call("Domain name,Domain type,Agency,Organization name,City,State,Security contact email\r\n"),
                call("cdomain11.gov,Federal - Executive,World War I Centennial Commission,,,,(blank)\r\n"),
                call("cdomain1.gov,Federal - Executive,World War I Centennial Commission,,,,(blank)\r\n"),
                call("adomain10.gov,Federal,Armed Forces Retirement Home,,,,(blank)\r\n"),
                call("ddomain3.gov,Federal,Armed Forces Retirement Home,,,,(blank)\r\n"),
                call("zdomain12.gov,Interstate,,,,,(blank)\r\n"),
            ]
            # We don't actually want to write anything for a test case,
            # we just want to verify what is being written.
            with boto3_mocking.clients.handler_for("s3", mock_client):
                with patch("builtins.open", fake_open):
                    call_command("generate_current_full_report", checkpath=False)
            content = fake_open()

            content.write.assert_has_calls(expected_file_content)

    @boto3_mocking.patching
    def test_not_found_full_report(self):
        """Ensures that we get a not found when the report doesn't exist"""

        def side_effect(Bucket, Key):
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "No such key"}}, "get_object")

        with less_console_noise():
            mock_client = MagicMock()
            mock_client.get_object.side_effect = side_effect

            response = None
            with boto3_mocking.clients.handler_for("s3", mock_client):
                with patch("boto3.client", return_value=mock_client):
                    with self.assertRaises(S3ClientError) as context:
                        response = self.client.get("/api/v1/get-report/current-full")
                        # Check that the response has status code 500
                        self.assertEqual(response.status_code, 500)

            # Check that we get the right error back from the page
            self.assertEqual(context.exception.code, S3ClientErrorCodes.FILE_NOT_FOUND_ERROR)

    @boto3_mocking.patching
    def test_not_found_federal_report(self):
        """Ensures that we get a not found when the report doesn't exist"""

        def side_effect(Bucket, Key):
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "No such key"}}, "get_object")

        with less_console_noise():
            mock_client = MagicMock()
            mock_client.get_object.side_effect = side_effect

            with boto3_mocking.clients.handler_for("s3", mock_client):
                with patch("boto3.client", return_value=mock_client):
                    with self.assertRaises(S3ClientError) as context:
                        response = self.client.get("/api/v1/get-report/current-federal")
                        # Check that the response has status code 500
                        self.assertEqual(response.status_code, 500)

            # Check that we get the right error back from the page
            self.assertEqual(context.exception.code, S3ClientErrorCodes.FILE_NOT_FOUND_ERROR)

    @boto3_mocking.patching
    def test_load_federal_report(self):
        """Tests the get_current_federal api endpoint"""

        with less_console_noise():
            mock_client = MagicMock()
            mock_client_instance = mock_client.return_value

            with open("registrar/tests/data/fake_current_federal.csv", "r") as file:
                file_content = file.read()

            # Mock a recieved file
            mock_client_instance.get_object.return_value = {"Body": io.BytesIO(file_content.encode())}
            with boto3_mocking.clients.handler_for("s3", mock_client):
                request = self.factory.get("/fake-path")
                response = get_current_federal(request)

            # Check that we are sending the correct calls.
            # Ensures that we are decoding the file content recieved from AWS.
            expected_call = [call.get_object(Bucket=settings.AWS_S3_BUCKET_NAME, Key="current-federal.csv")]
            mock_client_instance.assert_has_calls(expected_call)

            # Check that the response has status code 200
            self.assertEqual(response.status_code, 200)

            # Check that the response contains what we expect
            expected_file_content = (
                "Domain name,Domain type,Agency,Organization name,City,State,Security contact email\n"
                "cdomain1.gov,Federal - Executive,World War I Centennial Commission,,,,\n"
                "ddomain3.gov,Federal,Armed Forces Retirement Home,,,,"
            ).encode()

            self.assertEqual(expected_file_content, response.content)

    @boto3_mocking.patching
    def test_load_full_report(self):
        """Tests the current-federal api link"""

        with less_console_noise():
            mock_client = MagicMock()
            mock_client_instance = mock_client.return_value

            with open("registrar/tests/data/fake_current_full.csv", "r") as file:
                file_content = file.read()

            # Mock a recieved file
            mock_client_instance.get_object.return_value = {"Body": io.BytesIO(file_content.encode())}
            with boto3_mocking.clients.handler_for("s3", mock_client):
                request = self.factory.get("/fake-path")
                response = get_current_full(request)

            # Check that we are sending the correct calls.
            # Ensures that we are decoding the file content recieved from AWS.
            expected_call = [call.get_object(Bucket=settings.AWS_S3_BUCKET_NAME, Key="current-full.csv")]
            mock_client_instance.assert_has_calls(expected_call)

            # Check that the response has status code 200
            self.assertEqual(response.status_code, 200)

            # Check that the response contains what we expect
            expected_file_content = (
                "Domain name,Domain type,Agency,Organization name,City,State,Security contact email\n"
                "cdomain1.gov,Federal - Executive,World War I Centennial Commission,,,,\n"
                "ddomain3.gov,Federal,Armed Forces Retirement Home,,,,\n"
                "adomain2.gov,Interstate,,,,,"
            ).encode()

            self.assertEqual(expected_file_content, response.content)


class ExportDataTest(MockDbForIndividualTests, MockEppLib):
    """Test the ExportData class from csv_export."""

    @less_console_noise_decorator
    def test_domain_data_type(self):
        """Shows security contacts, domain managers, so"""

        # Add security email information
        self.domain_1.name = "defaultsecurity.gov"
        self.domain_1.save()
        # Invoke setter
        self.domain_1.security_contact
        # Invoke setter
        self.domain_2.security_contact
        # Invoke setter
        self.domain_3.security_contact
        # Add a first ready date on the first domain. Leaving the others blank.
        self.domain_1.first_ready = get_default_start_date()
        self.domain_1.save()
        # Create a CSV file in memory
        csv_file = StringIO()
        # Call the export functions
        DomainDataType.export_data_to_csv(csv_file)
        # Reset the CSV file's position to the beginning
        csv_file.seek(0)
        # Read the content into a variable
        csv_content = csv_file.read()
        # We expect READY domains,
        # sorted alphabetially by domain name
        expected_content = (
            "Domain name,Status,First ready on,Expiration date,Domain type,Agency,"
            "Organization name,City,State,SO,SO email,"
            "Security contact email,Domain managers,Invited domain managers\n"
            "adomain2.gov,Dns needed,(blank),(blank),Federal - Executive,"
            "Portfolio 1 Federal Agency,,,, ,,(blank),"
            "meoward@rocks.com,squeaker@rocks.com\n"
            "defaultsecurity.gov,Ready,2023-11-01,(blank),Federal - Executive,"
            "Portfolio 1 Federal Agency,,,, ,,(blank),"
            '"big_lebowski@dude.co, info@example.com, meoward@rocks.com",woofwardthethird@rocks.com\n'
            "cdomain11.gov,Ready,2024-04-02,(blank),Federal - Executive,"
            "World War I Centennial Commission,,,, ,,(blank),"
            "meoward@rocks.com,\n"
            "adomain10.gov,Ready,2024-04-03,(blank),Federal,Armed Forces Retirement Home,,,, ,,(blank),,"
            "squeaker@rocks.com\n"
            "bdomain4.gov,Unknown,(blank),(blank),Federal,Armed Forces Retirement Home,,,, ,,(blank),,\n"
            "bdomain5.gov,Deleted,(blank),(blank),Federal,Armed Forces Retirement Home,,,, ,,(blank),,\n"
            "bdomain6.gov,Deleted,(blank),(blank),Federal,Armed Forces Retirement Home,,,, ,,(blank),,\n"
            "ddomain3.gov,On hold,(blank),2023-11-15,Federal,"
            "Armed Forces Retirement Home,,,, ,,security@mail.gov,,\n"
            "sdomain8.gov,Deleted,(blank),(blank),Federal,Armed Forces Retirement Home,,,, ,,(blank),,\n"
            "xdomain7.gov,Deleted,(blank),(blank),Federal,Armed Forces Retirement Home,,,, ,,(blank),,\n"
            "zdomain9.gov,Deleted,(blank),(blank),Federal,Armed Forces Retirement Home,,,, ,,(blank),,\n"
            "zdomain12.gov,Ready,2024-04-02,(blank),Interstate,,,,, ,,(blank),meoward@rocks.com,\n"
        )

        # Normalize line endings and remove commas,
        # spaces and leading/trailing whitespace
        csv_content = csv_content.replace(",,", "").replace(",", "").replace(" ", "").replace("\r\n", "\n").strip()
        expected_content = expected_content.replace(",,", "").replace(",", "").replace(" ", "").strip()
        self.assertEqual(csv_content, expected_content)

    @less_console_noise_decorator
    def test_domain_data_type_user(self):
        """Shows security contacts, domain managers, so for the current user"""

        # Add security email information
        self.domain_1.name = "defaultsecurity.gov"
        self.domain_1.save()
        # Invoke setter
        self.domain_1.security_contact
        self.domain_2.security_contact
        self.domain_3.security_contact
        # Add a first ready date on the first domain. Leaving the others blank.
        self.domain_1.first_ready = get_default_start_date()
        self.domain_1.save()

        # Create a user and associate it with some domains
        UserDomainRole.objects.create(user=self.user, domain=self.domain_2)

        # Make a GET request using self.client to get a request object
        request = get_wsgi_request_object(client=self.client, user=self.user)

        # Create a CSV file in memory
        csv_file = StringIO()
        # Call the export functions
        DomainDataTypeUser.export_data_to_csv(csv_file, request=request)
        # Reset the CSV file's position to the beginning
        csv_file.seek(0)
        # Read the content into a variable
        csv_content = csv_file.read()

        # We expect only domains associated with the user
        expected_content = (
            "Domain name,Status,First ready on,Expiration date,Domain type,Agency,Organization name,"
            "City,State,SO,SO email,Security contact email,Domain managers,Invited domain managers\n"
            "adomain2.gov,Dns needed,(blank),(blank),Federal - Executive,Portfolio 1 Federal Agency,,,, ,,(blank),"
            '"info@example.com, meoward@rocks.com",squeaker@rocks.com\n'
            "defaultsecurity.gov,Ready,2023-11-01,(blank),Federal - Executive,Portfolio 1 Federal Agency,,,, ,,(blank),"
            '"big_lebowski@dude.co, info@example.com, meoward@rocks.com",woofwardthethird@rocks.com\n'
        )

        # Normalize line endings and remove commas,
        # spaces and leading/trailing whitespace
        csv_content = csv_content.replace(",,", "").replace(",", "").replace(" ", "").replace("\r\n", "\n").strip()
        expected_content = expected_content.replace(",,", "").replace(",", "").replace(" ", "").strip()
        self.assertEqual(csv_content, expected_content)

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    def test_domain_data_type_user_with_portfolio(self):
        """Tests DomainDataTypeUser export with portfolio permissions"""

        # Create a portfolio and assign it to the user
        portfolio = Portfolio.objects.create(creator=self.user, organization_name="Test Portfolio")
        portfolio_permission, _ = UserPortfolioPermission.objects.get_or_create(portfolio=portfolio, user=self.user)

        UserDomainRole.objects.create(user=self.user, domain=self.domain_2)
        UserDomainRole.objects.filter(user=self.user, domain=self.domain_1).delete()
        UserDomainRole.objects.filter(user=self.user, domain=self.domain_3).delete()

        # Add portfolios to the first and third domains
        self.domain_1.domain_info.portfolio = portfolio
        self.domain_3.domain_info.portfolio = portfolio

        self.domain_1.domain_info.save()
        self.domain_3.domain_info.save()

        # Set up user permissions
        portfolio_permission.roles = [UserPortfolioRoleChoices.ORGANIZATION_ADMIN]
        portfolio_permission.save()
        portfolio_permission.refresh_from_db()

        # Make a GET request using self.client to get a request object
        request = get_wsgi_request_object(client=self.client, user=self.user)

        # Get the csv content
        csv_content = self._run_domain_data_type_user_export(request)

        # We expect only domains associated with the user's portfolio
        self.assertIn(self.domain_1.name, csv_content)
        self.assertIn(self.domain_3.name, csv_content)
        self.assertNotIn(self.domain_2.name, csv_content)

        # Get the csv content
        csv_content = self._run_domain_data_type_user_export(request)
        self.assertIn(self.domain_1.name, csv_content)
        self.assertIn(self.domain_3.name, csv_content)
        self.assertNotIn(self.domain_2.name, csv_content)

        portfolio_permission.roles = [UserPortfolioRoleChoices.ORGANIZATION_MEMBER]
        portfolio_permission.save()
        portfolio_permission.refresh_from_db()

        # Get the csv content
        csv_content = self._run_domain_data_type_user_export(request)
        self.assertNotIn(self.domain_1.name, csv_content)
        self.assertNotIn(self.domain_3.name, csv_content)
        self.assertIn(self.domain_2.name, csv_content)
        self.domain_1.delete()
        self.domain_2.delete()
        self.domain_3.delete()
        portfolio.delete()

    def _run_domain_data_type_user_export(self, request):
        """Helper function to run the export_data_to_csv function on DomainDataTypeUser"""
        # Create a CSV file in memory
        csv_file = StringIO()
        # Call the export functions
        DomainDataTypeUser.export_data_to_csv(csv_file, request=request)
        # Reset the CSV file's position to the beginning
        csv_file.seek(0)
        # Read the content into a variable
        csv_content = csv_file.read()

        return csv_content

    @less_console_noise_decorator
    @override_flag("organization_feature", active=True)
    @override_flag("organization_requests", active=True)
    def test_domain_request_data_type_user_with_portfolio(self):
        """Tests DomainRequestsDataType export with portfolio permissions"""

        # Create a portfolio and assign it to the user
        portfolio = Portfolio.objects.create(creator=self.user, organization_name="Test Portfolio")
        portfolio_permission, _ = UserPortfolioPermission.objects.get_or_create(portfolio=portfolio, user=self.user)

        # Create DraftDomain objects
        dd_1 = DraftDomain.objects.create(name="example1.com")
        dd_2 = DraftDomain.objects.create(name="example2.com")
        dd_3 = DraftDomain.objects.create(name="example3.com")

        # Create some domain requests
        dr_1 = DomainRequest.objects.create(creator=self.user, requested_domain=dd_1, portfolio=portfolio)
        dr_2 = DomainRequest.objects.create(creator=self.user, requested_domain=dd_2)
        dr_3 = DomainRequest.objects.create(creator=self.user, requested_domain=dd_3, portfolio=portfolio)

        # Set up user permissions
        portfolio_permission.roles = [UserPortfolioRoleChoices.ORGANIZATION_ADMIN]
        portfolio_permission.save()
        portfolio_permission.refresh_from_db()

        # Make a GET request using self.client to get a request object
        request = get_wsgi_request_object(client=self.client, user=self.user)

        # Get the CSV content
        csv_content = self._run_domain_request_data_type_user_export(request)

        # We expect only domain requests associated with the user's portfolio
        self.assertIn(dd_1.name, csv_content)
        self.assertIn(dd_3.name, csv_content)
        self.assertNotIn(dd_2.name, csv_content)

        # Get the csv content
        csv_content = self._run_domain_request_data_type_user_export(request)
        self.assertIn(dd_1.name, csv_content)
        self.assertIn(dd_3.name, csv_content)
        self.assertNotIn(dd_2.name, csv_content)

        portfolio_permission.roles = [UserPortfolioRoleChoices.ORGANIZATION_MEMBER]
        portfolio_permission.save()
        portfolio_permission.refresh_from_db()

        # Domain Request NOT in Portfolio
        csv_content = self._run_domain_request_data_type_user_export(request)
        self.assertNotIn(dd_1.name, csv_content)
        self.assertNotIn(dd_3.name, csv_content)
        self.assertNotIn(dd_2.name, csv_content)

        # Clean up the created objects
        dr_1.delete()
        dr_2.delete()
        dr_3.delete()
        portfolio.delete()

    def _run_domain_request_data_type_user_export(self, request):
        """Helper function to run the export_data_to_csv function on DomainRequestDataType"""

        csv_file = StringIO()

        DomainRequestDataType.export_data_to_csv(csv_file, request=request)

        csv_file.seek(0)

        csv_content = csv_file.read()

        return csv_content

    @less_console_noise_decorator
    def test_domain_data_full(self):
        """Shows security contacts, filtered by state"""
        # Add security email information
        self.domain_1.name = "defaultsecurity.gov"
        self.domain_1.save()
        # Invoke setter
        self.domain_1.security_contact
        # Invoke setter
        self.domain_3.security_contact
        # Add a first ready date on the first domain. Leaving the others blank.
        self.domain_1.first_ready = get_default_start_date()
        self.domain_1.save()
        # Create a CSV file in memory
        csv_file = StringIO()
        # Call the export functions
        DomainDataFull.export_data_to_csv(csv_file)
        # Reset the CSV file's position to the beginning
        csv_file.seek(0)
        # Read the content into a variable
        csv_content = csv_file.read()
        # We expect READY domains,
        # sorted alphabetially by domain name
        expected_content = (
            "Domain name,Domain type,Agency,Organization name,City,State,Security contact email\n"
            "cdomain11.gov,Federal - Executive,World War I Centennial Commission,,,,(blank)\n"
            "defaultsecurity.gov,Federal - Executive,World War I Centennial Commission,,,,(blank)\n"
            "adomain10.gov,Federal,Armed Forces Retirement Home,,,,(blank)\n"
            "ddomain3.gov,Federal,Armed Forces Retirement Home,,,,security@mail.gov\n"
            "zdomain12.gov,Interstate,,,,,(blank)\n"
        )
        # Normalize line endings and remove commas,
        # spaces and leading/trailing whitespace
        csv_content = csv_content.replace(",,", "").replace(",", "").replace(" ", "").replace("\r\n", "\n").strip()
        expected_content = expected_content.replace(",,", "").replace(",", "").replace(" ", "").strip()
        self.maxDiff = None
        self.assertEqual(csv_content, expected_content)

    @less_console_noise_decorator
    def test_domain_data_federal(self):
        """Shows security contacts, filtered by state and org type"""
        # Add security email information
        self.domain_1.name = "defaultsecurity.gov"
        self.domain_1.save()
        # Invoke setter
        self.domain_1.security_contact
        # Invoke setter
        self.domain_2.security_contact
        # Invoke setter
        self.domain_3.security_contact
        # Add a first ready date on the first domain. Leaving the others blank.
        self.domain_1.first_ready = get_default_start_date()
        self.domain_1.save()
        # Create a CSV file in memory
        csv_file = StringIO()
        # Call the export functions
        DomainDataFederal.export_data_to_csv(csv_file)
        # Reset the CSV file's position to the beginning
        csv_file.seek(0)
        # Read the content into a variable
        csv_content = csv_file.read()
        # We expect READY domains,
        # sorted alphabetially by domain name
        expected_content = (
            "Domain name,Domain type,Agency,Organization name,City,State,Security contact email\n"
            "cdomain11.gov,Federal - Executive,World War I Centennial Commission,,,,(blank)\n"
            "defaultsecurity.gov,Federal - Executive,World War I Centennial Commission,,,,(blank)\n"
            "adomain10.gov,Federal,Armed Forces Retirement Home,,,,(blank)\n"
            "ddomain3.gov,Federal,Armed Forces Retirement Home,,,,security@mail.gov\n"
        )
        # Normalize line endings and remove commas,
        # spaces and leading/trailing whitespace
        csv_content = csv_content.replace(",,", "").replace(",", "").replace(" ", "").replace("\r\n", "\n").strip()
        expected_content = expected_content.replace(",,", "").replace(",", "").replace(" ", "").strip()
        self.maxDiff = None
        self.assertEqual(csv_content, expected_content)

    @less_console_noise_decorator
    def test_domain_growth(self):
        """Shows ready and deleted domains within a date range, sorted"""
        # Remove "Created at" and "First ready" because we can't guess this immutable, dynamically generated test data
        columns = [
            "Domain name",
            "Domain type",
            "Agency",
            "Organization name",
            "City",
            "State",
            "Status",
            "Expiration date",
            # "Created at",
            # "First ready",
            "Deleted",
        ]
        sort = {
            "custom_sort": Case(
                When(domain__state=Domain.State.READY, then="domain__created_at"),
                When(domain__state=Domain.State.DELETED, then="domain__deleted"),
            )
        }
        with patch("registrar.utility.csv_export.DomainGrowth.get_columns", return_value=columns):
            with patch("registrar.utility.csv_export.DomainGrowth.get_annotations_for_sort", return_value=sort):
                # Create a CSV file in memory
                csv_file = StringIO()
                # Call the export functions
                DomainGrowth.export_data_to_csv(
                    csv_file,
                    start_date=self.start_date.strftime("%Y-%m-%d"),
                    end_date=self.end_date.strftime("%Y-%m-%d"),
                )
                # Reset the CSV file's position to the beginning
                csv_file.seek(0)
                # Read the content into a variable
                csv_content = csv_file.read()
                # We expect READY domains first, created between day-2 and day+2, sorted by created_at then name
                # and DELETED domains deleted between day-2 and day+2, sorted by deleted then name
                expected_content = (
                    "Domain name,Domain type,Agency,Organization name,City,"
                    "State,Status,Expiration date, Deleted\n"
                    "cdomain1.gov,Federal-Executive,Portfolio1FederalAgency,Ready,(blank)\n"
                    "adomain10.gov,Federal,ArmedForcesRetirementHome,Ready,(blank)\n"
                    "cdomain11.gov,Federal-Executive,WorldWarICentennialCommission,Ready,(blank)\n"
                    "zdomain12.gov,Interstate,Ready,(blank)\n"
                    "zdomain9.gov,Federal,ArmedForcesRetirementHome,Deleted,(blank),2024-04-01\n"
                    "sdomain8.gov,Federal,ArmedForcesRetirementHome,Deleted,(blank),2024-04-02\n"
                    "xdomain7.gov,Federal,ArmedForcesRetirementHome,Deleted,(blank),2024-04-02\n"
                )
                # Normalize line endings and remove commas,
                # spaces and leading/trailing whitespace
                csv_content = (
                    csv_content.replace(",,", "").replace(",", "").replace(" ", "").replace("\r\n", "\n").strip()
                )
                expected_content = expected_content.replace(",,", "").replace(",", "").replace(" ", "").strip()
                self.assertEqual(csv_content, expected_content)

    @less_console_noise_decorator
    def test_domain_managed(self):
        """Shows ready and deleted domains by an end date, sorted

        An invited user, woofwardthethird, should also be pulled into this report.

        squeaker@rocks.com is invited to domain2 (DNS_NEEDED) and domain10 (No managers).
        She should show twice in this report but not in test_DomainManaged."""
        # Create a CSV file in memory
        csv_file = StringIO()
        # Call the export functions
        DomainManaged.export_data_to_csv(
            csv_file,
            start_date=self.start_date.strftime("%Y-%m-%d"),
            end_date=self.end_date.strftime("%Y-%m-%d"),
        )
        # Reset the CSV file's position to the beginning
        csv_file.seek(0)
        # Read the content into a variable
        csv_content = csv_file.read()
        # We expect the READY domain names with the domain managers: Their counts, and listing at end_date.
        expected_content = (
            "MANAGED DOMAINS COUNTS AT START DATE\n"
            "Total,Federal,Interstate,State or territory,Tribal,County,City,Special district,"
            "School district,Election office\n"
            "0,0,0,0,0,0,0,0,0,0\n"
            "\n"
            "MANAGED DOMAINS COUNTS AT END DATE\n"
            "Total,Federal,Interstate,State or territory,Tribal,County,City,"
            "Special district,School district,Election office\n"
            "3,2,1,0,0,0,0,0,0,0\n"
            "\n"
            "Domain name,Domain type,Domain managers,Invited domain managers\n"
            "cdomain11.gov,Federal - Executive,meoward@rocks.com,\n"
            'cdomain1.gov,Federal - Executive,"big_lebowski@dude.co, info@example.com, meoward@rocks.com",'
            "woofwardthethird@rocks.com\n"
            "zdomain12.gov,Interstate,meoward@rocks.com,\n"
        )
        # Normalize line endings and remove commas,
        # spaces and leading/trailing whitespace
        csv_content = csv_content.replace(",,", "").replace(",", "").replace(" ", "").replace("\r\n", "\n").strip()
        expected_content = expected_content.replace(",,", "").replace(",", "").replace(" ", "").strip()
        self.assertEqual(csv_content, expected_content)

    @less_console_noise_decorator
    def test_domain_unmanaged(self):
        """Shows unmanaged domains by an end date, sorted"""
        # Create a CSV file in memory
        csv_file = StringIO()
        DomainUnmanaged.export_data_to_csv(
            csv_file, start_date=self.start_date.strftime("%Y-%m-%d"), end_date=self.end_date.strftime("%Y-%m-%d")
        )

        # Reset the CSV file's position to the beginning
        csv_file.seek(0)
        # Read the content into a variable
        csv_content = csv_file.read()

        # We expect the READY domain names with the domain managers: Their counts, and listing at end_date.
        expected_content = (
            "UNMANAGED DOMAINS AT START DATE\n"
            "Total,Federal,Interstate,State or territory,Tribal,County,City,Special district,"
            "School district,Election office\n"
            "0,0,0,0,0,0,0,0,0,0\n"
            "\n"
            "UNMANAGED DOMAINS AT END DATE\n"
            "Total,Federal,Interstate,State or territory,Tribal,County,City,Special district,"
            "School district,Election office\n"
            "1,1,0,0,0,0,0,0,0,0\n"
            "\n"
            "Domain name,Domain type\n"
            "adomain10.gov,Federal\n"
        )

        # Normalize line endings and remove commas,
        # spaces and leading/trailing whitespace
        csv_content = csv_content.replace(",,", "").replace(",", "").replace(" ", "").replace("\r\n", "\n").strip()
        expected_content = expected_content.replace(",,", "").replace(",", "").replace(" ", "").strip()
        self.assertEqual(csv_content, expected_content)

    @less_console_noise_decorator
    def test_domain_request_growth(self):
        """Shows submitted requests within a date range, sorted"""
        # Remove "Submitted at" because we can't guess this immutable, dynamically generated test data
        columns = [
            "Domain request",
            "Domain type",
            "Federal type",
            # "Submitted at",
        ]
        with patch("registrar.utility.csv_export.DomainRequestGrowth.get_columns", return_value=columns):
            # Create a CSV file in memory
            csv_file = StringIO()
            # Call the export functions
            DomainRequestGrowth.export_data_to_csv(
                csv_file,
                start_date=self.start_date.strftime("%Y-%m-%d"),
                end_date=self.end_date.strftime("%Y-%m-%d"),
            )
            # Reset the CSV file's position to the beginning
            csv_file.seek(0)
            # Read the content into a variable
            csv_content = csv_file.read()

            expected_content = (
                "Domain request,Domain type,Federal type\n"
                "city3.gov,Federal,Executive\n"
                "city4.gov,City,Executive\n"
                "city6.gov,Federal,Executive\n"
            )

            # Normalize line endings and remove commas,
            # spaces and leading/trailing whitespace
            csv_content = csv_content.replace(",,", "").replace(",", "").replace(" ", "").replace("\r\n", "\n").strip()
            expected_content = expected_content.replace(",,", "").replace(",", "").replace(" ", "").strip()
            self.assertEqual(csv_content, expected_content)

    # @less_console_noise_decorator
    def test_domain_request_data_full(self):
        """Tests the full domain request report."""
        # Remove "Submitted at" because we can't guess this immutable, dynamically generated test data
        columns = [
            "Domain request",
            # "Submitted at",
            "Status",
            "Domain type",
            "Portfolio",
            "Federal type",
            "Federal agency",
            "Organization name",
            "Election office",
            "City",
            "State/territory",
            "Region",
            "Suborganization",
            "Requested suborg",
            "Suborg city",
            "Suborg state/territory",
            "Creator first name",
            "Creator last name",
            "Creator email",
            "Creator approved domains count",
            "Creator active requests count",
            "Alternative domains",
            "SO first name",
            "SO last name",
            "SO email",
            "SO title/role",
            "Request purpose",
            "Request additional details",
            "Other contacts",
            "CISA regional representative",
            "Current websites",
            "Investigator",
        ]
        with patch("registrar.utility.csv_export.DomainRequestDataFull.get_columns", return_value=columns):
            # Create a CSV file in memory
            csv_file = StringIO()
            # Call the export functions
            DomainRequestDataFull.export_data_to_csv(csv_file)
            # Reset the CSV file's position to the beginning
            csv_file.seek(0)
            # Read the content into a variable
            csv_content = csv_file.read()

            expected_content = (
                # Header
                "Domain request,Status,Domain type,Portfolio,Federal type,Federal agency,Organization name,"
                "Election office,City,State/territory,Region,Suborganization,Requested suborg,Suborg city,"
                "Suborg state/territory,Creator first name,Creator last name,Creator email,"
                "Creator approved domains count,Creator active requests count,Alternative domains,SO first name,"
                "SO last name,SO email,SO title/role,Request purpose,Request additional details,Other contacts,"
                "CISA regional representative,Current websites,Investigator\n"
                # Content
                "city5.gov,Approved,Federal,No,Executive,,Testorg,N/A,,NY,2,requested_suborg,SanFran,CA,,,,,1,0,"
                "city1.gov,Testy,Tester,testy@town.com,Chief Tester,Purpose of the site,There is more,"
                "Testy Tester testy2@town.com,,city.com,\n"
                "city2.gov,In review,Federal,Yes,Executive,Portfolio 1 Federal Agency,,N/A,,,2,SubOrg 1,,,,,,,0,"
                "1,city1.gov,,,,,Purpose of the site,There is more,Testy Tester testy2@town.com,,city.com,\n"
                "city3.gov,Submitted,Federal,Yes,Executive,Portfolio 1 Federal Agency,,N/A,,,2,,,,,,,,0,1,"
                '"cheeseville.gov, city1.gov, igorville.gov",,,,,Purpose of the site,CISA-first-name CISA-last-name | '
                'There is more,"Meow Tester24 te2@town.com, Testy1232 Tester24 te2@town.com, '
                'Testy Tester testy2@town.com",'
                'test@igorville.com,"city.com, https://www.example2.com, https://www.example.com",\n'
                "city4.gov,Submitted,City,No,Executive,,Testorg,Yes,,NY,2,,,,,,,,0,1,city1.gov,Testy,"
                "Tester,testy@town.com,"
                "Chief Tester,Purpose of the site,CISA-first-name CISA-last-name | There is more,"
                "Testy Tester testy2@town.com,"
                "cisaRep@igorville.gov,city.com,\n"
                "city6.gov,Submitted,Federal,Yes,Executive,Portfolio 1 Federal Agency,,N/A,,,2,,,,,,,,0,1,city1.gov,"
                ",,,,Purpose of the site,CISA-first-name CISA-last-name | There is more,Testy Tester testy2@town.com,"
                "cisaRep@igorville.gov,city.com,\n"
            )

            # Normalize line endings and remove commas,
            # spaces and leading/trailing whitespace
            csv_content = csv_content.replace(",,", "").replace(",", "").replace(" ", "").replace("\r\n", "\n").strip()
            expected_content = expected_content.replace(",,", "").replace(",", "").replace(" ", "").strip()
            self.maxDiff = None
            self.assertEqual(csv_content, expected_content)


class MemberExportTest(MockDbForIndividualTests, MockEppLib):

    def setUp(self):
        """Override of the base setUp to add a request factory"""
        super().setUp()
        self.factory = RequestFactory()

    @override_flag("organization_feature", active=True)
    @override_flag("organization_members", active=True)
    @less_console_noise_decorator
    def test_member_export(self):
        """Tests the member export report by comparing the csv output."""
        # == Data setup == #
        # Set last_login for some users
        active_date = timezone.make_aware(datetime(2024, 2, 1))
        User.objects.filter(id__in=[self.custom_superuser.id, self.custom_staffuser.id]).update(last_login=active_date)

        # Create a logentry for meoward, created by lebowski to test invited_by.
        content_type = ContentType.objects.get_for_model(PortfolioInvitation)
        LogEntry.objects.create(
            user=self.lebowski_user,
            content_type=content_type,
            object_id=self.portfolio_invitation_1.id,
            object_repr=str(self.portfolio_invitation_1),
            action_flag=ADDITION,
            change_message="Created invitation",
            action_time=timezone.make_aware(datetime(2023, 4, 12)),
        )

        # Create log entries for each remaining invitation. Exclude meoward and tired_user.
        for invitation in PortfolioInvitation.objects.exclude(
            id__in=[self.portfolio_invitation_1.id, self.portfolio_invitation_3.id]
        ):
            LogEntry.objects.create(
                user=self.custom_staffuser,
                content_type=content_type,
                object_id=invitation.id,
                object_repr=str(invitation),
                action_flag=ADDITION,
                change_message="Created invitation",
                action_time=timezone.make_aware(datetime(2024, 1, 15)),
            )

        # Retrieve invitations
        with boto3_mocking.clients.handler_for("sesv2", self.mock_client_class):
            self.meoward_user.check_portfolio_invitations_on_login()
            self.lebowski_user.check_portfolio_invitations_on_login()
            self.tired_user.check_portfolio_invitations_on_login()
            self.custom_superuser.check_portfolio_invitations_on_login()
            self.custom_staffuser.check_portfolio_invitations_on_login()

        # Update the created at date on UserPortfolioPermission, so we can test a consistent date.
        UserPortfolioPermission.objects.filter(portfolio=self.portfolio_1).update(
            created_at=timezone.make_aware(datetime(2022, 4, 1))
        )
        # == End of data setup == #

        # Create a request and add the user to the request
        request = self.factory.get("/")
        request.user = self.user
        # Add portfolio to session
        request = GenericTestHelper._mock_user_request_for_factory(request)
        request.session["portfolio"] = self.portfolio_1

        # Create a CSV file in memory
        csv_file = StringIO()
        # Call the export function
        MemberExport.export_data_to_csv(csv_file, request=request)
        # Reset the CSV file's position to the beginning
        csv_file.seek(0)
        # Read the content into a variable
        csv_content = csv_file.read()
        expected_content = (
            # Header
            "Email,Organization admin,Invited by,Joined date,Last active,Domain requests,"
            "Member management,Domain management,Number of domains,Domains\n"
            # Content
            "big_lebowski@dude.co,False,help@get.gov,2022-04-01,Invalid date,None,"
            "Viewer,True,1,cdomain1.gov\n"
            "cozy_staffuser@igorville.gov,True,help@get.gov,2022-04-01,2024-02-01,"
            "Viewer,Viewer,False,0,\n"
            "icy_superuser@igorville.gov,True,help@get.gov,2022-04-01,2024-02-01,"
            "Viewer Requester,Manager,False,0,\n"
            "meoward@rocks.com,False,big_lebowski@dude.co,2022-04-01,Invalid date,None,"
            'Manager,True,2,"adomain2.gov,cdomain1.gov"\n'
            "nonexistentmember_1@igorville.gov,False,help@get.gov,Unretrieved,Invited,"
            "None,Manager,False,0,\n"
            "nonexistentmember_2@igorville.gov,False,help@get.gov,Unretrieved,Invited,"
            "None,Viewer,False,0,\n"
            "nonexistentmember_3@igorville.gov,False,help@get.gov,Unretrieved,Invited,"
            "Viewer,None,False,0,\n"
            "nonexistentmember_4@igorville.gov,True,help@get.gov,Unretrieved,Invited,"
            "Viewer Requester,Manager,False,0,\n"
            "nonexistentmember_5@igorville.gov,True,help@get.gov,Unretrieved,Invited,"
            "Viewer,Viewer,False,0,\n"
            "tired_sleepy@igorville.gov,False,System,2022-04-01,Invalid date,Viewer,"
            "None,False,0,\n"
        )
        # Normalize line endings and remove commas,
        # spaces and leading/trailing whitespace
        csv_content = csv_content.replace(",,", "").replace(",", "").replace(" ", "").replace("\r\n", "\n").strip()
        expected_content = expected_content.replace(",,", "").replace(",", "").replace(" ", "").strip()
        self.maxDiff = None
        self.assertEqual(csv_content, expected_content)


class HelperFunctions(MockDbForSharedTests):
    """This asserts that 1=1. Its limited usefulness lies in making sure the helper methods stay healthy."""

    def test_get_default_start_date(self):
        expected_date = get_time_aware_date()
        actual_date = get_default_start_date()
        self.assertEqual(actual_date, expected_date)

    def test_get_default_end_date(self):
        # Note: You may need to mock timezone.now() for accurate testing
        expected_date = timezone.now()
        actual_date = get_default_end_date()
        self.assertEqual(actual_date.date(), expected_date.date())

    def test_get_sliced_domains(self):
        """Should get fitered domains counts sliced by org type and election office."""

        with less_console_noise():
            filter_condition = {
                "domain__permissions__isnull": False,
                "domain__first_ready__lte": self.end_date,
            }
            # Test with distinct
            managed_domains_sliced_at_end_date = DomainExport.get_sliced_domains(filter_condition)
            expected_content = [3, 2, 1, 0, 0, 0, 0, 0, 0, 0]
            self.assertEqual(managed_domains_sliced_at_end_date, expected_content)

            # Test without distinct
            managed_domains_sliced_at_end_date = DomainExport.get_sliced_domains(filter_condition)
            expected_content = [3, 2, 1, 0, 0, 0, 0, 0, 0, 0]
            self.assertEqual(managed_domains_sliced_at_end_date, expected_content)

    def test_get_sliced_requests(self):
        """Should get fitered requests counts sliced by org type and election office."""

        with less_console_noise():
            filter_condition = {
                "status": DomainRequest.DomainRequestStatus.SUBMITTED,
                "last_submitted_date__lte": self.end_date,
            }
            submitted_requests_sliced_at_end_date = DomainRequestExport.get_sliced_requests(filter_condition)
            expected_content = [3, 2, 0, 0, 0, 0, 1, 0, 0, 1]
            self.assertEqual(submitted_requests_sliced_at_end_date, expected_content)
