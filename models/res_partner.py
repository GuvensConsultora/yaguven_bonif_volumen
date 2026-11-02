from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    vd_tier_ids = fields.One2many(
        "yaguven.volume.discount.tier", "partner_id",
        string="Escala particular de bonificación",
        help="Si el cliente tiene escala propia en una empresa, reemplaza a la escala de la "
             "lista de precios que tiene asignada.",
    )
