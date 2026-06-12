from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="payment",
            name="payment_method",
            field=models.CharField(
                choices=[
                    ("legacy", "Legacy"),
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
