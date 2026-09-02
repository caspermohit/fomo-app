// FOMO chart helper for MetaTrader 5.
// Does not trade. Python run.py is the only bot (magic 26082601).
// Keep this attached only if you want a chart comment. Prefer FOMO_Analyst for arrows.

#property copyright "fomo-app demo desk"
#property version   "2.00"
#property strict
#property description "Display only. Python is the only FOMO bot. This Expert does not send orders."

#define MUTEX_EA "FOMO_EA_ENGINE_ACTIVE"

void ClearOldEaLock()
{
   FileDelete(MUTEX_EA);
   if(GlobalVariableCheck(MUTEX_EA))
      GlobalVariableDel(MUTEX_EA);
}

int OnInit()
{
   if((ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE) != ACCOUNT_TRADE_MODE_DEMO)
   {
      Print("FOMO helper refuses a non-demo account.");
      return INIT_FAILED;
   }
   ClearOldEaLock();
   if(!EventSetTimer(1))
   {
      Print("FOMO helper failed to start the 1-second timer.");
      return INIT_FAILED;
   }
   Print("FOMO helper on ", _Symbol, " — Python is the only bot. No OrderSend.");
   Comment("FOMO desk: Python is the only bot. This chart helper does not trade.");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   ClearOldEaLock();
   Comment("");
}

void OnTimer()
{
   ClearOldEaLock();
   Comment("FOMO desk: Python is the only bot (run.py + dashboard). This helper does not send orders.");
}

void OnTick()
{
}
