# Generated by Django 4.2.10 on 2024-03-20 21:14

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("registrar", "0077_alter_publiccontact_fax_alter_publiccontact_org_and_more"),
    ]

    operations = [
        migrations.RenameField(
            model_name="domaininformation",
            old_name="organization_type",
            new_name="generic_org_type",
        ),
        migrations.RenameField(
            model_name="domainrequest",
            old_name="organization_type",
            new_name="generic_org_type",
        ),
        migrations.RenameField(
            model_name="transitiondomain",
            old_name="organization_type",
            new_name="generic_org_type",
        ),
    ]
