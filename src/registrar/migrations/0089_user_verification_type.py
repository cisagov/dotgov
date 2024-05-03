# Generated by Django 4.2.10 on 2024-04-26 14:03

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("registrar", "0088_domaininformation_cisa_representative_email_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="verification_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("grandfathered", "Legacy user"),
                    ("verified_by_staff", "Verified by staff"),
                    ("regular", "Verified by Login.gov"),
                    ("invited", "Invited by a domain manager"),
                    ("fixture_user", "Created by fixtures"),
                ],
                help_text="The means through which this user was verified",
                null=True,
            ),
        ),
    ]
