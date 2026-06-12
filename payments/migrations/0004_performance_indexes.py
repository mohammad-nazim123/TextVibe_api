from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0003_alter_payment_payment_method"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="tokenpackage",
            index=models.Index(fields=["is_active", "amount"], name="tokenpkg_active_amt_idx"),
        ),
        migrations.AddIndex(
            model_name="payment",
            index=models.Index(fields=["user", "-created_at"], name="pay_user_created_idx"),
        ),
    ]
