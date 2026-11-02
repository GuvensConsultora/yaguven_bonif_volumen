from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    vd_product_id = fields.Many2one(
        "product.product", string="Producto de bonificación",
        domain="[('type', '=', 'service')]",
        help="Producto de servicio que se usa en la línea de descuento de la factura y en "
             "las líneas de la nota de crédito. De él salen la cuenta contable y los impuestos.",
    )
    vd_base = fields.Selection(
        [("untaxed", "Importe bruto sin impuestos"), ("total", "Importe bruto con impuestos")],
        string="Monto que se compara contra la escala",
        help="Importe bruto: cantidad por precio unitario, antes de los descuentos de cada "
             "línea. Elegí si se mide sin o con impuestos. Sobre ese mismo importe se calcula "
             "el % de bonificación.",
    )
    vd_excluded_product_ids = fields.Many2many(
        "product.product", "yaguven_vd_company_excluded_product_rel", "company_id", "product_id",
        string="Productos que no suman",
        help="Productos que no cuentan para el acumulado del mes ni reciben bonificación, "
             "por ejemplo el flete.",
    )
    vd_only_paid = fields.Boolean(
        string="Contar sólo facturas cobradas",
        help="Si está marcado, las facturas anteriores del mes suman al acumulado y reciben "
             "la nota de crédito sólo cuando están cobradas.",
    )
