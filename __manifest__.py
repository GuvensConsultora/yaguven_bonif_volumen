{
    "name": "Yagüven Bonificación por Volumen",
    "version": "19.0.1.0.0",
    "summary": "Bonificación por monto de compra mensual: escala por empresa y por cliente, "
               "línea de descuento en la factura que cruza el tramo y NC por las anteriores",
    "description": """
Bonificación por volumen de compra en el mes calendario.

* Escala de tramos (monto desde → %) sin límite de niveles, general por empresa y
  particular por cliente (la del cliente reemplaza a la general).
* Al confirmar una factura de cliente, calcula lo facturado en el mes a ese cliente
  (neto de notas de crédito) más la factura en curso. Si cruza un tramo, abre un
  asistente con el historial del mes y la bonificación que corresponde.
* Aplica la bonificación como línea de descuento en la factura en curso y arma una
  nota de crédito en borrador con una línea por cada factura anterior del mes.
* Al pasar a un tramo superior, bonifica sólo la diferencia sobre lo ya aplicado.

Construido por herencia sobre account.move, sin reemplazar acciones del core.
    """,
    "author": "Yagüven C.G.",
    "website": "https://yaguvencg.com.ar",
    "license": "LGPL-3",
    "category": "Accounting/Accounting",
    "depends": ["account"],
    "data": [
        "security/ir.model.access.csv",
        "views/volume_discount_tier_views.xml",
        "views/res_company_views.xml",
        "views/res_partner_views.xml",
        "wizard/volume_discount_wizard_views.xml",
    ],
    "installable": True,
    "application": False,
}
