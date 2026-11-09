"""Synthetic replies shaped like the 06/11 probe. Masked data must never pass as real data."""

from app.erp_local import channel_recipients as cr

CPF = "52998224725"


def ml_reply(**overrides):
    billing = {
        "billing_info": {
            "doc_type": "CPF",
            "doc_number": CPF,
            "additional_info": [
                {"type": "FIRST_NAME", "value": "Ana"},
                {"type": "LAST_NAME", "value": "Souza"},
            ],
        }
    }
    shipment = {
        "destination": {
            "receiver_name": "Bia Souza",
            "receiver_phone": "31 99999-0000",
            "shipping_address": {
                "street_name": "Rua Exemplo",
                "street_number": "20",
                "comment": "Casa",
                "neighborhood": {"name": "Centro"},
                "city": {"name": "Belo Horizonte"},
                "state": {"id": "BR-MG"},
                "zip_code": "30110-000",
                **overrides,
            },
        }
    }
    return billing, shipment


def test_ml_keeps_buyer_and_receiver_apart_and_resolves_city_code():
    result = cr.from_ml(*ml_reply())
    assert result["ready"] is True
    assert result["buyer"] == {"name": "Ana Souza", "document": CPF}
    assert result["recipient"]["name"] == "Bia Souza" and result["recipient"]["state"] == "MG"
    assert result["recipient"]["ibge_code"] == "3106200"
    assert result["recipient"]["postal_code"] == "30110000"


def test_ml_company_name_wins_and_missing_number_becomes_explicit():
    billing, shipment = ml_reply(street_number="")
    billing["billing_info"]["additional_info"].append(
        {"type": "BUSINESS_NAME", "value": "Empresa LTDA"}
    )
    result = cr.from_ml(billing, shipment)
    assert result["buyer"]["name"] == "Empresa LTDA" and result["recipient"]["number"] == "S/N"


def test_masked_values_are_treated_as_absent_and_block_the_order():
    billing, shipment = ml_reply(street_name="R** E*****", city={"name": "B*** H*******"})
    billing["billing_info"]["doc_number"] = "***.***.247-25"
    result = cr.from_ml(billing, shipment)
    assert result["ready"] is False
    assert {"documento", "logradouro", "município"} <= set(result["missing"])
    assert "*" not in str(result["buyer"]) and "*" not in str(result["recipient"])


def test_city_outside_the_official_table_blocks_instead_of_guessing():
    result = cr.from_ml(*ml_reply(city={"name": "Cidade Inventada"}))
    assert result["ready"] is False and "município sem código oficial" in result["missing"]


def test_magalu_reads_customer_and_delivery_recipient():
    order = {
        "customer": {"name": "Ana", "document_number": CPF},
        "deliveries": [
            {
                "shipping": {
                    "recipient": {
                        "name": "Bia",
                        "address": {
                            "street": "Rua A",
                            "number": "1",
                            "district": "Centro",
                            "city": "São Paulo",
                            "state": "SP",
                            "zipcode": "01001000",
                        },
                    }
                }
            }
        ],
    }
    result = cr.from_magalu(order)
    assert result["ready"] is True and result["recipient"]["ibge_code"] == "3550308"


def test_tiktok_uses_cpf_fields_and_ignores_the_masked_phone():
    order = {
        "cpf": CPF,
        "cpf_name": "Ana",
        "recipient_address": {
            "name": "Bia",
            "phone_number": "(+55)31*****00",
            "address_line1": "Rua A",
            "address_line2": "20",
            "address_line3": "Centro",
            "postal_code": "30110000",
            "district_info": [
                {"address_level_name": "Country", "address_name": "Brasil", "iso_code": "BR"},
                {"address_level_name": "State", "address_name": "Minas", "iso_code": "BR-MG"},
                {"address_level_name": "City", "address_name": "Belo Horizonte"},
            ],
        },
    }
    result = cr.from_tiktok(order)
    assert result["ready"] is True and result["buyer"]["document"] == CPF
    assert result["recipient"]["state"] == "MG" and result["recipient"]["phone"] == ""


def test_empty_reply_lists_everything_missing():
    result = cr.from_tiktok({})
    assert result["ready"] is False and {"documento", "nome", "CEP"} <= set(result["missing"])
