from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    vd_tier_ids = fields.One2many(
        "yaguven.volume.discount.tier", "company_id",
        string="Escala general de bonificación",
        domain=[("partner_id", "=", False)],
    )
    vd_product_id = fields.Many2one(
        "product.product", string="Producto de bonificación",
        domain="[('type', '=', 'service')]",
        help="Producto de servicio que se usa en la línea de descuento de la factura y en "
             "las líneas de la nota de crédito. De él salen la cuenta contable y los impuestos.",
    )
    vd_base = fields.Selection(
        [("untaxed", "Neto sin impuestos"), ("total", "Total con impuestos")],
        string="Monto que se compara contra la escala",
        help="Si el tope de la escala se mide sobre el neto o sobre el total de las facturas.",
    )
    vd_only_paid = fields.Boolean(
        string="Contar sólo facturas cobradas",
        help="Si está marcado, las facturas anteriores del mes suman al acumulado y reciben "
             "la nota de crédito sólo cuando están cobradas.",
    )
