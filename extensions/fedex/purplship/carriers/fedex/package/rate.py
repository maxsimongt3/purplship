from datetime import datetime
from typing import Tuple, List, Optional, cast
from pyfedex.rate_service_v26 import (
    RateRequest as FedexRateRequest,
    RateReplyDetail,
    TransactionDetail,
    VersionId,
    RequestedShipment,
    TaxpayerIdentification,
    Party,
    Contact,
    Address,
    Money,
    Weight as FedexWeight,
    ShipmentRateDetail,
    Surcharge,
    RequestedPackageLineItem,
    Dimensions
)
from purplship.core.utils import export, concat_str, Serializable, format_date, decimal
from purplship.core.utils.soap import create_envelope
from purplship.core.units import Package, Options
from purplship.core.utils.xml import Element
from purplship.core.errors import RequiredFieldError
from purplship.core.models import RateDetails, RateRequest, Message, ChargeDetails
from purplship.carriers.fedex.units import PackagingType, ServiceType, PackagePresets
from purplship.carriers.fedex.error import parse_error_response
from purplship.carriers.fedex.utils import Settings


def parse_rate_response(
    response: Element, settings: Settings
) -> Tuple[List[RateDetails], List[Message]]:
    rate_reply = response.xpath(".//*[local-name() = $name]", name="RateReplyDetails")
    rate_details: List[RateDetails] = [
        _extract_rate(detail_node, settings) for detail_node in rate_reply
    ]
    return (
        [details for details in rate_details if details is not None],
        parse_error_response(response, settings),
    )


def _extract_rate(detail_node: Element, settings: Settings) -> Optional[RateDetails]:
    rate: RateReplyDetail = RateReplyDetail()
    rate.build(detail_node)

    service = ServiceType(rate.ServiceType).name
    rate_type = rate.ActualRateType
    shipment_rate, shipment_discount = cast(Tuple[ShipmentRateDetail, Money], next(
        (
            (r.ShipmentRateDetail, r.EffectiveNetDiscount) for r in rate.RatedShipmentDetails
            if cast(ShipmentRateDetail, r.ShipmentRateDetail).RateType == rate_type
        ),
        (None, None)
    ))
    discount = decimal(shipment_discount.Amount) if shipment_discount is not None else None
    currency = cast(Money, shipment_rate.TotalBaseCharge).Currency
    duties_and_taxes = shipment_rate.TotalTaxes.Amount + shipment_rate.TotalDutiesAndTaxes.Amount
    surcharges = [
        ChargeDetails(
            name=cast(Surcharge, s).Description,
            amount=decimal(cast(Surcharge, s).Amount.Amount),
            currency=currency
        )
        for s in shipment_rate.Surcharges + shipment_rate.Taxes
    ]

    return RateDetails(
        carrier=settings.carrier,
        carrier_name=settings.carrier_name,
        service=service,
        currency=currency,
        base_charge=decimal(shipment_rate.TotalBaseCharge.Amount),
        total_charge=decimal(shipment_rate.TotalNetChargeWithDutiesAndTaxes.Amount),
        duties_and_taxes=decimal(duties_and_taxes),
        discount=discount,
        estimated_delivery=format_date(rate.DeliveryTimestamp, "%Y-%m-%d %H:%M:%S"),
        extra_charges=surcharges,
    )


def rate_request(
    payload: RateRequest, settings: Settings
) -> Serializable[FedexRateRequest]:
    parcel_preset = (
        PackagePresets[payload.parcel.package_preset].value
        if payload.parcel.package_preset
        else None
    )
    package = Package(payload.parcel, parcel_preset)

    if package.weight.value is None:
        raise RequiredFieldError("parcel.weight")

    is_international = payload.shipper.country_code != payload.recipient.country_code
    service = next(
        (
            ServiceType[s].value
            for s in payload.services
            if s in ServiceType.__members__
        ),
        ServiceType.international_first.value if is_international else ServiceType.standard_overnight.value,
    )
    options = Options(payload.options)

    request = FedexRateRequest(
        WebAuthenticationDetail=settings.webAuthenticationDetail,
        ClientDetail=settings.clientDetail,
        TransactionDetail=TransactionDetail(CustomerTransactionId="FTC"),
        Version=VersionId(ServiceId="crs", Major=26, Intermediate=0, Minor=0),
        ReturnTransitAndCommit=True,
        CarrierCodes=None,
        VariableOptions=None,
        ConsolidationKey=None,
        RequestedShipment=RequestedShipment(
            ShipTimestamp=datetime.now(),
            DropoffType="REGULAR_PICKUP",
            ServiceType=service,
            PackagingType=PackagingType[
                package.packaging_type or "your_packaging"
            ].value,
            VariationOptions=None,
            TotalWeight=FedexWeight(
                Units=package.weight_unit.value, Value=package.weight.value,
            ),
            TotalInsuredValue=None,
            PreferredCurrency=options.currency,
            ShipmentAuthorizationDetail=None,
            Shipper=Party(
                AccountNumber=settings.account_number,
                Tins=[
                    TaxpayerIdentification(TinType=None, Number=tax)
                    for tax in [
                        payload.shipper.federal_tax_id,
                        payload.shipper.state_tax_id,
                    ]
                ]
                if any([payload.shipper.federal_tax_id, payload.shipper.state_tax_id])
                else None,
                Contact=Contact(
                    ContactId=None,
                    PersonName=payload.shipper.person_name,
                    Title=None,
                    CompanyName=payload.shipper.company_name,
                    PhoneNumber=payload.shipper.phone_number,
                    PhoneExtension=None,
                    TollFreePhoneNumber=None,
                    PagerNumber=None,
                    FaxNumber=None,
                    EMailAddress=payload.shipper.email,
                )
                if any(
                    (
                        payload.shipper.company_name,
                        payload.shipper.person_name,
                        payload.shipper.phone_number,
                        payload.shipper.email,
                    )
                )
                else None,
                Address=Address(
                    StreetLines=concat_str(
                        payload.shipper.address_line1, payload.shipper.address_line2
                    ),
                    City=payload.shipper.city,
                    StateOrProvinceCode=payload.shipper.state_code,
                    PostalCode=payload.shipper.postal_code,
                    UrbanizationCode=None,
                    CountryCode=payload.shipper.country_code,
                    CountryName=None,
                    Residential=None,
                    GeographicCoordinates=None,
                ),
            ),
            Recipient=Party(
                AccountNumber=None,
                Tins=[
                    TaxpayerIdentification(TinType=None, Number=tax)
                    for tax in [
                        payload.recipient.federal_tax_id,
                        payload.recipient.state_tax_id,
                    ]
                ]
                if any(
                    [payload.recipient.federal_tax_id, payload.recipient.state_tax_id]
                )
                else None,
                Contact=Contact(
                    ContactId=None,
                    PersonName=payload.recipient.person_name,
                    Title=None,
                    CompanyName=payload.recipient.company_name,
                    PhoneNumber=payload.recipient.phone_number,
                    PhoneExtension=None,
                    TollFreePhoneNumber=None,
                    PagerNumber=None,
                    FaxNumber=None,
                    EMailAddress=payload.recipient.email,
                )
                if any(
                    (
                        payload.recipient.company_name,
                        payload.recipient.person_name,
                        payload.recipient.phone_number,
                        payload.recipient.email,
                    )
                )
                else None,
                Address=Address(
                    StreetLines=concat_str(
                        payload.recipient.address_line1,
                        payload.recipient.address_line2,
                    ),
                    City=payload.recipient.city,
                    StateOrProvinceCode=payload.recipient.state_code,
                    PostalCode=payload.recipient.postal_code,
                    UrbanizationCode=None,
                    CountryCode=payload.recipient.country_code,
                    CountryName=None,
                    Residential=None,
                    GeographicCoordinates=None,
                ),
            ),
            RecipientLocationNumber=None,
            Origin=None,
            SoldTo=None,
            ShippingChargesPayment=None,
            SpecialServicesRequested=None,
            ExpressFreightDetail=None,
            FreightShipmentDetail=None,
            DeliveryInstructions=None,
            VariableHandlingChargeDetail=None,
            CustomsClearanceDetail=None,
            PickupDetail=None,
            SmartPostDetail=None,
            BlockInsightVisibility=None,
            LabelSpecification=None,
            ShippingDocumentSpecification=None,
            RateRequestTypes=["LIST"]
            + ([] if "currency" not in payload.options else ["PREFERRED"]),
            EdtRequestType=None,
            PackageCount=None,
            ShipmentOnlyFields=None,
            ConfigurationData=None,
            RequestedPackageLineItems=[
                RequestedPackageLineItem(
                    SequenceNumber=1,
                    GroupNumber=None,
                    GroupPackageCount=1,
                    VariableHandlingChargeDetail=None,
                    InsuredValue=None,
                    Weight=FedexWeight(
                        Units=package.weight_unit.value,
                        Value=package.weight.value,
                    )
                    if package.weight.value is not None
                    else None,
                    Dimensions=Dimensions(
                        Length=package.length.value,
                        Width=package.width.value,
                        Height=package.height.value,
                        Units=package.dimension_unit.value,
                    )
                    if any([package.length.value, package.width.value, package.height.value])
                    else None,
                    PhysicalPackaging=None,
                    ItemDescription=payload.parcel.description,
                    ItemDescriptionForClearance=None,
                    CustomerReferences=None,
                    SpecialServicesRequested=None,
                    ContentRecords=None,
                )
            ],
        ),
    )
    return Serializable(request, _request_serializer)


def _request_serializer(request: FedexRateRequest) -> str:
    return export(
        create_envelope(body_content=request),
        namespacedef_='xmlns:tns="http://schemas.xmlsoap.org/soap/envelope/" xmlns:SOAP-ENC="http://schemas.xmlsoap.org/soap/encoding/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns="http://fedex.com/ws/rate/v26"',
    ).replace('tns:RateRequest', 'RateRequest')