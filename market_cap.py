"""
Calcul de market cap — bonding curve (pré-migration) ET pAMM (post-migration,
nécessaire car un dev-buy de 71-78 SOL à la création pousse quasi certainement la
MC bien au-dessus du seuil de migration pump.fun (~$69-100k) avant même la 1ère
vente du dev qui déclenche notre achat). Formule bonding curve : le ratio
virtual_sol/virtual_token EST directement le prix utilisé par le programme (pas
de vaults séparées à corriger). Formule pAMM : ratio brut des vaults de pool,
connu pour diverger du vrai prix exécutable — biais empirique séparé, à utiliser
seulement en repli de `pamm.simulate_mc_usd`. Porté de Bundle10k/market_cap.py.
"""
import trading_constants as tc

MC_BIAS_MARGIN = 1.0
PAMM_MC_BIAS_MARGIN = 1.051


def bonding_curve_mc_usd(virtual_sol_reserves: int, virtual_token_reserves: int, sol_price_usd: float) -> float:
    if virtual_token_reserves <= 0:
        return 0.0
    price_sol = (virtual_sol_reserves / 10 ** tc.SOL_DECIMALS) / (virtual_token_reserves / 10 ** tc.TOKEN_DECIMALS)
    return price_sol * tc.PUMP_SUPPLY * sol_price_usd * MC_BIAS_MARGIN


def pamm_mc_usd(sol_in_pool: float, tokens_in_pool: float, sol_price_usd: float) -> float:
    if tokens_in_pool <= 0:
        return 0.0
    price_sol = sol_in_pool / tokens_in_pool
    return price_sol * tc.PUMP_SUPPLY * sol_price_usd * PAMM_MC_BIAS_MARGIN


def quote_min_tokens_out(
    spendable_sol_in_lamports: int,
    virtual_sol_reserves: int,
    virtual_token_reserves: int,
    slippage_pct: float,
    total_fee_bps: int,
) -> int:
    """
    Calcule min_tokens_out pour buy_exact_quote_in_v2 : combien de tokens on
    exige au minimum pour spendable_sol_in_lamports, en supposant que le prix a
    DÉJÀ monté de slippage_pct par rapport à la dernière lecture connue des
    réserves (pire cas pessimiste). La dépense (spendable_sol_in_lamports) reste
    toujours EXACTE et plafonnée quel que soit slippage_pct — seul
    min_tokens_out varie, donc jamais de risque de survente.
    """
    effective_vsr = int(virtual_sol_reserves * (1 + slippage_pct))
    net_sol = (spendable_sol_in_lamports * 10_000) // (10_000 + total_fee_bps)
    if net_sol <= 1:
        return 0
    tokens_out = ((net_sol - 1) * virtual_token_reserves) // (effective_vsr + net_sol - 1)
    return max(0, int(tokens_out))
