from django.forms import Select


class ComboboxWidget(Select):
    template_name = "django/forms/widgets/combobox.html"
