from collections.abc import Mapping

from rest_framework import serializers


class StrictFieldsMixin:
    """Rejeita chaves desconhecidas no payload em vez de ignorá-las.

    O DRF por padrão descarta campos que não conhece. Isso esconde erros de
    digitação no cliente ("asignee_id") e tentativas de gravar campos
    controlados pelo servidor ("creator", "identifier").
    """

    def to_internal_value(self, data):
        if isinstance(data, Mapping):
            writable = {name for name, field in self.fields.items() if not field.read_only}
            unknown = sorted(set(data) - writable)
            if unknown:
                raise serializers.ValidationError({key: ["Campo não reconhecido ou somente leitura."] for key in unknown})
        return super().to_internal_value(data)
