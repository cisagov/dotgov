# Generated by Django 4.2.1 on 2023-09-27 18:53

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("registrar", "0033_usergroup"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="user",
            options={
                "permissions": [
                    ("analyst_access_permission", "Analyst Access Permission"),
                    ("full_access_permission", "Full Access Permission"),
                ]
            },
        ),
    ]
