from odoo import fields, models


class ProductPricelist(models.Model):
    _inherit = "product.pricelist"

    vd_enabled = fields.Boolean(
        string="Bonificación por volumen mensual",
        help="Si está marcada, los clientes que tienen esta lista asignada en su ficha reciben "
             "la bonificación según los tramos de abajo, calculada sobre lo facturado en el "
             "mes calendario. Un cliente con escala propia usa la suya en lugar de ésta.",
    )
    vd_tier_ids = fields.One2many(
        "yaguven.volume.discount.tier", "pricelist_id",
        string="Tramos de bonificación por volumen",
    )
