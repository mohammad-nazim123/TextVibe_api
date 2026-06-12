from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0002_payment_payment_method"),
    ]

    operations = [
        migrations.AlterField(
            model_name="payment",
            name="payment_method",
            field=models.CharField(
                choices=[
                    ("legacy", "Legacy"),
                    ("google_play", "Google Play"),
                    ("upi", "UPI"),
                    ("card", "Cards"),
                    ("netbanking", "Net Banking"),
                    ("wallet", "Wallets"),
                ],
                default="legacy",
                max_length=20,
            ),
        ),
    ]
