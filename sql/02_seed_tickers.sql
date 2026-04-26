-- QualMaggie Momentum Swing Trading System
-- Step 2: Seed 20 starter tickers
-- Run after 01_schema.sql. Safe to re-run (uses MERGE).

USE QualMaggie;
GO

MERGE dbo.Tickers AS target
USING (VALUES
--  Symbol   CompanyName                                    Sector                    Exchange  IndexMembership
    ('AAPL',  'Apple Inc.',                                 'Technology',             'Nasdaq', 'Both'),
    ('MSFT',  'Microsoft Corporation',                      'Technology',             'Nasdaq', 'Both'),
    ('NVDA',  'NVIDIA Corporation',                         'Technology',             'Nasdaq', 'Both'),
    ('META',  'Meta Platforms Inc.',                        'Communication Services', 'Nasdaq', 'Both'),
    ('GOOGL', 'Alphabet Inc.',                              'Communication Services', 'Nasdaq', 'Both'),
    ('AMZN',  'Amazon.com Inc.',                            'Consumer Discretionary', 'Nasdaq', 'Both'),
    ('AVGO',  'Broadcom Inc.',                              'Technology',             'Nasdaq', 'Both'),
    ('TSM',   'Taiwan Semiconductor Manufacturing Co.',     'Technology',             'NYSE',   'SP500'),
    ('AMD',   'Advanced Micro Devices Inc.',                'Technology',             'Nasdaq', 'Both'),
    ('CRWD',  'CrowdStrike Holdings Inc.',                  'Technology',             'Nasdaq', 'Both'),
    ('PANW',  'Palo Alto Networks Inc.',                    'Technology',             'Nasdaq', 'Both'),
    ('AXON',  'Axon Enterprise Inc.',                       'Technology',             'Nasdaq', 'SP500'),
    ('MELI',  'MercadoLibre Inc.',                          'Consumer Discretionary', 'Nasdaq', 'Nasdaq100'),
    ('TTD',   'The Trade Desk Inc.',                        'Technology',             'Nasdaq', 'Nasdaq100'),
    ('SMCI',  'Super Micro Computer Inc.',                  'Technology',             'Nasdaq', 'Nasdaq100'),
    ('CELH',  'Celsius Holdings Inc.',                      'Consumer Staples',       'Nasdaq', 'Nasdaq100'),
    ('DECK',  'Deckers Outdoor Corporation',                'Consumer Discretionary', 'NYSE',   'SP500'),
    ('LULU',  'Lululemon Athletica Inc.',                   'Consumer Discretionary', 'Nasdaq', 'Both'),
    ('ENPH',  'Enphase Energy Inc.',                        'Technology',             'Nasdaq', 'Both'),
    ('DXCM',  'DexCom Inc.',                                'Healthcare',             'Nasdaq', 'Both')
) AS source (Symbol, CompanyName, Sector, Exchange, IndexMembership)
ON target.Symbol = source.Symbol
WHEN MATCHED THEN
    UPDATE SET
        CompanyName     = source.CompanyName,
        Sector          = source.Sector,
        Exchange        = source.Exchange,
        IndexMembership = source.IndexMembership,
        IsActive        = 1
WHEN NOT MATCHED BY TARGET THEN
    INSERT (Symbol, CompanyName, Sector, Exchange, IndexMembership)
    VALUES (source.Symbol, source.CompanyName, source.Sector, source.Exchange, source.IndexMembership);
GO

-- Verify
SELECT TickerID, Symbol, Sector, Exchange, IndexMembership
FROM   dbo.Tickers
ORDER  BY TickerID;
GO
