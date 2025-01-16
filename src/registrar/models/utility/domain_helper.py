import re
from typing import Type
from django.db import models
from django import forms
from django.http import JsonResponse

from api.views import DOMAIN_API_MESSAGES, check_domain_available
from registrar.utility import errors
from epplibwrapper.errors import RegistryError
from registrar.utility.enums import ValidationReturnType


class DomainHelper:
    """Utility functions and constants for domain names."""

    # a domain name is alphanumeric or hyphen, up to 63 characters, doesn't
    # begin or end with a hyphen, followed by a TLD of 2-6 alphabetic characters
    DOMAIN_REGEX = re.compile(r"^(?!-)[A-Za-z0-9-]{1,200}(?<!-)\.[A-Za-z]{2,6}$")

    # a domain can be no longer than 253 characters in total
    # NOTE: the domain name is limited by the DOMAIN_REGEX above
    # to 200 characters (not including the .gov at the end)
    MAX_LENGTH = 253

    @classmethod
    def string_could_be_domain(cls, domain: str | None) -> bool:
        """Return True if the string could be a domain name, otherwise False."""
        if not isinstance(domain, str):
            return False
        return bool(cls.DOMAIN_REGEX.match(domain))

    @classmethod
    def validate(cls, domain: str, blank_ok=False) -> str:
        """Attempt to determine if a domain name could be requested."""
        # Split into pieces for the linter
        domain = cls._validate_domain_string(domain, blank_ok)

        if domain != "":
            try:
                if not check_domain_available(domain):
                    raise errors.DomainUnavailableError()
            except RegistryError as err:
                raise errors.RegistrySystemError() from err
        return domain

    @staticmethod
    def _validate_domain_string(domain, blank_ok):
        """Normalize the domain string, and check its content"""
        if domain is None:
            raise errors.BlankValueError()

        if not isinstance(domain, str):
            raise errors.InvalidDomainError()

        domain = domain.lower().strip()

        if domain == "" and not blank_ok:
            raise errors.BlankValueError()
        elif domain == "":
            # If blank ok is true, just return the domain
            return domain

        if domain.startswith("www."):
            domain = domain[4:]

        if domain.endswith(".gov"):
            domain = domain[:-4]

        if "." in domain:
            raise errors.ExtraDotsError()

        if not DomainHelper.string_could_be_domain(domain + ".gov"):
            raise errors.InvalidDomainError()

        return domain

    @classmethod
    def validate_and_handle_errors(cls, domain, return_type, blank_ok=False):
        """
        Validates a domain and returns an appropriate response based on the validation result.

        This method uses the `validate` method to validate the domain. If validation fails, it catches the exception,
        maps it to a corresponding error code, and returns a response based on the `return_type` parameter.

        Args:
            domain (str): The domain to validate.
            return_type (ValidationReturnType): Determines the type of response (JSON or form validation error).
            blank_ok (bool, optional): If True, blank input does not raise an exception. Defaults to False.

        Returns:
            tuple: The validated domain (or None if validation failed), and the response (success or error).
        """  # noqa

        # Map each exception to a corresponding error code
        error_map = {
            errors.BlankValueError: "required",
            errors.ExtraDotsError: "extra_dots",
            errors.DomainUnavailableError: "unavailable",
            errors.RegistrySystemError: "error",
            errors.InvalidDomainError: "invalid",
        }

        validated = None
        response = None

        try:
            # Attempt to validate the domain
            validated = cls.validate(domain, blank_ok)

        # Get a list of each possible exception, and the code to return
        except tuple(error_map.keys()) as error:
            # If an error is caught, get its type
            error_type = type(error)

            # Generate the response based on the error code and return type
            response = DomainHelper._return_form_error_or_json_response(return_type, code=error_map.get(error_type))
        else:
            # For form validation, we do not need to display the success message
            if return_type != ValidationReturnType.FORM_VALIDATION_ERROR:
                response = DomainHelper._return_form_error_or_json_response(return_type, code="success", available=True)

        # Return the validated domain and the response (either error or success)
        return (validated, response)

    @staticmethod
    def _return_form_error_or_json_response(return_type: ValidationReturnType, code, available=False):
        """
        Returns an error response based on the `return_type`.

        If `return_type` is `FORM_VALIDATION_ERROR`, raises a form validation error.
        If `return_type` is `JSON_RESPONSE`, returns a JSON response with 'available', 'code', and 'message' fields.
        If `return_type` is neither, raises a ValueError.

        Args:
            return_type (ValidationReturnType): The type of error response.
            code (str): The error code for the error message.
            available (bool, optional): Availability, only used for JSON responses. Defaults to False.

        Returns:
            A JSON response or a form validation error.

        Raises:
            ValueError: If `return_type` is neither `FORM_VALIDATION_ERROR` nor `JSON_RESPONSE`.
        """  # noqa
        match return_type:
            case ValidationReturnType.FORM_VALIDATION_ERROR:
                raise forms.ValidationError(DOMAIN_API_MESSAGES[code], code=code)
            case ValidationReturnType.JSON_RESPONSE:
                return JsonResponse({"available": available, "code": code, "message": DOMAIN_API_MESSAGES[code]})
            case _:
                raise ValueError("Invalid return type specified")

    @classmethod
    def sld(cls, domain: str):
        """
        Get the second level domain. Example: `gsa.gov` -> `gsa`.

        If no TLD is present, returns the original string.
        """
        return domain.split(".")[0]

    @classmethod
    def tld(cls, domain: str):
        """Get the top level domain. Example: `gsa.gov` -> `gov`."""
        parts = domain.rsplit(".")
        return parts[-1] if len(parts) > 1 else ""

    @staticmethod
    def get_common_fields(model_1: Type[models.Model], model_2: Type[models.Model]):
        """
        Returns a set of field names that two Django models have in common, excluding the 'id' field.

        Args:
            model_1 (Type[models.Model]): The first Django model class.
            model_2 (Type[models.Model]): The second Django model class.

        Returns:
            Set[str]: A set of field names that both models share.

        Example:
            If model_1 has fields {"id", "name", "color"} and model_2 has fields {"id", "color"},
            the function will return {"color"}.
        """

        # Get a list of the existing fields on model_1 and model_2
        model_1_fields = set(field.name for field in model_1._meta.get_fields() if field.name != "id")
        model_2_fields = set(field.name for field in model_2._meta.get_fields() if field.name != "id")

        # Get the fields that exist on both DomainRequest and DomainInformation
        common_fields = model_1_fields & model_2_fields

        return common_fields

    @staticmethod
    def mass_disable_fields(fields, disable_required=False, disable_maxlength=False):
        """
        Given some fields, invoke .disabled = True on them.
        disable_required: bool -> invokes .required = False on each field.
        disable_maxlength: bool -> pops "maxlength" from each field.
        """
        for field in fields.values():
            field = DomainHelper.disable_field(field, disable_required, disable_maxlength)
        return fields

    @staticmethod
    def disable_field(field, disable_required=False, disable_maxlength=False):
        """
        Given a fields, invoke .disabled = True on it.
        disable_required: bool -> invokes .required = False for the field.
        disable_maxlength: bool -> pops "maxlength" for the field.
        """
        field.disabled = True

        if disable_required:
            # if a field is disabled, it can't be required
            field.required = False

        if disable_maxlength:
            # Remove the maxlength dialog
            if "maxlength" in field.widget.attrs:
                field.widget.attrs.pop("maxlength", None)
        return field
