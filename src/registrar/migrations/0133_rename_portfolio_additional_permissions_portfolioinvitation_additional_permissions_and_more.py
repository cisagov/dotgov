# Generated by Django 4.2.10 on 2024-10-08 19:05

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("registrar", "0132_alter_domaininformation_portfolio_and_more"),
    ]

    operations = [
        migrations.RenameField(
            model_name="portfolioinvitation",
            old_name="portfolio_additional_permissions",
            new_name="additional_permissions",
        ),
        migrations.RenameField(
            model_name="portfolioinvitation",
            old_name="portfolio_roles",
            new_name="roles",
        ),
    ]