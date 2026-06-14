from django.core.management.base import BaseCommand

from payments.models import TokenPackage

PACKAGES = [
    {"tokens": 100, "amount": 1},
    {"tokens": 300, "amount": 2},
    {"tokens": 500, "amount": 3},
]


class Command(BaseCommand):
    help = "Seed the 3 default token packages (100/₹1, 300/₹2, 500/₹3)"

    def handle(self, *args, **options):
        created = 0
        for pkg in PACKAGES:
            obj, is_new = TokenPackage.objects.update_or_create(
                tokens=pkg["tokens"],
                defaults={"amount": pkg["amount"], "is_active": True},
            )
            if is_new:
                created += 1
                self.stdout.write(
                    self.style.SUCCESS(f"  Created: {obj.tokens} tokens for ₹{obj.amount}")
                )
            else:
                self.stdout.write(f"  Updated: {obj.tokens} tokens for ₹{obj.amount}")

        self.stdout.write(
            self.style.SUCCESS(f"\nDone. {created} package(s) created, {len(PACKAGES) - created} updated.")
        )
