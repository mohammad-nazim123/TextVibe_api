from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0009_post_background_id_post_frame_id"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="post",
            index=models.Index(fields=["-created_at"], name="post_created_desc_idx"),
        ),
        migrations.AddIndex(
            model_name="post",
            index=models.Index(fields=["user", "-created_at"], name="post_user_created_idx"),
        ),
    ]
