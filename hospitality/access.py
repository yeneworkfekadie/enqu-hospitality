from .models import Hotel


def hotels_for_user(user):
    if user.is_superuser:
        return Hotel.objects.all()
    if not user.is_authenticated:
        return Hotel.objects.none()
    return Hotel.objects.filter(staff_memberships__user=user)
