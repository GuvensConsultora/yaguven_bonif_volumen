from odoo import api, fields, models
from odoo.exceptions import ValidationError


class VolumeDiscountTier(models.Model):
    """Tramo de la escala de bonificación por volumen mensual.

    Cada tramo pertenece a UNA de dos escalas:
    * Con lista de precios: escala de esa lista. Rige para los clientes que tienen esa
      lista asignada en su ficha, siempre que la lista tenga activada la bonificación.
    * Con cliente: escala particular de ese cliente, que reemplaza completa a la de su
      lista (no se mezclan tramos de las dos).
    """

    _name = "yaguven.volume.discount.tier"
    _description = "Tramo de bonificación por volumen"
    _order = "company_id, pricelist_id, partner_id, amount_from"

    company_id = fields.Many2one(
        "res.company", string="Empresa", required=True, index=True,
        default=lambda self: self.env.company,
    )
    pricelist_id = fields.Many2one(
        "product.pricelist", string="Lista de precios", index="btree_not_null",
        ondelete="cascade",
    )
    partner_id = fields.Many2one(
        "res.partner", string="Cliente", index="btree_not_null", ondelete="cascade",
        domain="[('is_company', '=', True)]",
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    amount_from = fields.Monetary(
        string="Compra mensual desde", required=True,
        help="Monto facturado en el mes calendario a partir del cual rige el porcentaje.",
    )
    percent = fields.Float(
        string="Bonificación (%)", required=True, digits=(5, 2),
        help="Porcentaje total que corresponde al llegar a este monto (no se suma al del "
             "tramo anterior: al pasar de 4% a 8% se bonifica la diferencia).",
    )

    _tier_unique = models.UniqueIndex(
        "(company_id, COALESCE(pricelist_id, 0), COALESCE(partner_id, 0), amount_from)",
        "Ya existe un tramo con ese monto en la misma escala.",
    )

    # amount_from está en la lista para que la validación corra también en el alta: Odoo
    # sólo valida al crear los campos que vienen cargados, y un tramo sin lista ni cliente
    # no trae ninguno de los dos.
    @api.constrains("pricelist_id", "partner_id", "amount_from")
    def _check_owner(self):
        for tier in self:
            if bool(tier.pricelist_id) == bool(tier.partner_id):
                raise ValidationError(self.env._(
                    "Cada tramo pertenece a una lista de precios o a un cliente, no a los dos "
                    "ni a ninguno."
                ))

    @api.constrains("amount_from", "percent")
    def _check_values(self):
        for tier in self:
            if tier.amount_from <= 0:
                raise ValidationError(self.env._("El monto del tramo tiene que ser mayor a cero."))
            if not 0 < tier.percent < 100:
                raise ValidationError(self.env._("El porcentaje tiene que estar entre 0 y 100."))

    @api.onchange("pricelist_id")
    def _onchange_pricelist_company(self):
        if self.pricelist_id.company_id:
            self.company_id = self.pricelist_id.company_id

    @api.model
    def _get_scale(self, company, partner):
        """Escala vigente para (empresa, cliente).

        1. La escala particular del cliente en esa empresa, si tiene.
        2. Si no, la de la lista de precios asignada en su ficha, si esa lista tiene
           activada la bonificación por volumen.
        La lista del contacto es company_dependent: se lee con la empresa de la factura.
        """
        partner = partner.commercial_partner_id
        own = self.search([("company_id", "=", company.id), ("partner_id", "=", partner.id)])
        if own:
            return own
        pricelist = partner.with_company(company).property_product_pricelist
        if not pricelist.vd_enabled:
            return self.browse()
        return pricelist.vd_tier_ids.filtered(lambda t: t.company_id == company)

    @api.model
    def _percent_for(self, scale, amount):
        """Porcentaje del tramo más alto alcanzado por `amount` (0 si no llega a ninguno)."""
        reached = scale.filtered(lambda t: amount >= t.amount_from)
        return max(reached.mapped("percent"), default=0.0)
