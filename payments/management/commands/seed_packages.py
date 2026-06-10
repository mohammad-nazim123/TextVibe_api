from django.core.management.base import BaseCommand

from payments.models import TokenPackage

PACKAGES = [
    {"tokens": 100, "amount": 25},
    {"tokens": 300, "amount": 60},
    {"tokens": 500, "amount": 100},
]


class Command(BaseCommand):
    help = "Seed the 3 default token packages (100/₹25, 300/₹60, 500/₹100)"

    def handle(self, *args, **options):
        created = 0
        for pkg in PACKAGES:
            obj, is_new = TokenPackage.objects.get_or_create(
                tokens=pkg["tokens"],
                defaults={"amount": pkg["amount"], "is_active": True},
            )
            if is_new:
                created += 1
                self.stdout.write(
                    self.style.SUCCESS(f"  Created: {obj.tokens} tokens for ₹{obj.amount}")
                )
            else:
                self.stdout.write(f"  Exists:  {obj.tokens} tokens for ₹{obj.amount}")

        self.stdout.write(
            self.style.SUCCESS(f"\nDone. {created} package(s) created, {len(PACKAGES) - created} already existed.")
        )
