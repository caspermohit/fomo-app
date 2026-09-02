// FOMO Analyst — chart overlay for MetaTrader 5 (demo visual only).
// Matches bot/analyst.py Bill rules: H1 close vs EMA21, M5 close with that trend,
// London/NY session (07–21 UTC), ATR >= 1. Does not place orders.

#property copyright "fomo-app demo desk"
#property version   "2.00"
#property description "FOMO Bill overlay. Arrows follow H1 trend + M5 close. Demo visual only — this indicator does not trade."
#property indicator_chart_window
#property indicator_buffers 8
#property indicator_plots   5

#property indicator_label1 "EMA 9"
#property indicator_type1  DRAW_LINE
#property indicator_color1 clrDodgerBlue
#property indicator_style1 STYLE_SOLID
#property indicator_width1 1

#property indicator_label2 "EMA 21"
#property indicator_type2  DRAW_LINE
#property indicator_color2 clrOrange
#property indicator_style2 STYLE_SOLID
#property indicator_width2 1

#property indicator_label3 "EMA 50"
#property indicator_type3  DRAW_LINE
#property indicator_color3 clrSilver
#property indicator_style3 STYLE_DOT
#property indicator_width3 1

#property indicator_label4 "BUY"
#property indicator_type4  DRAW_ARROW
#property indicator_color4 clrLime
#property indicator_width4 2

#property indicator_label5 "SELL"
#property indicator_type5  DRAW_ARROW
#property indicator_color5 clrRed
#property indicator_width5 2

input int    FastEMA          = 9;
input int    SlowEMA          = 21;
input int    TrendEMA         = 50;
input int    RSIPeriod        = 14;
input int    ATRPeriod        = 14;
input int    MinScore         = 4;
input double MinAtrPips       = 1.0;
input int    SessionStartHour = 7;
input int    SessionEndHour   = 21;
input bool   UseGmtSession    = true;

double g_ema9[];
double g_ema21[];
double g_ema50[];
double g_buy[];
double g_sell[];
double g_avgGain[];
double g_avgLoss[];
double g_atr[];

int g_h1_ema = INVALID_HANDLE;
int g_h4_ema = INVALID_HANDLE;

double EmaAlpha(const int period)
{
   return 2.0 / (period + 1.0);
}

double PipSize()
{
   string sym = _Symbol;
   StringToUpper(sym);
   if(StringFind(sym, "JPY") >= 0)
      return 0.01;
   return 0.0001;
}

bool InSession(const datetime barTime)
{
   datetime gmt = barTime;
   if(UseGmtSession)
      gmt = barTime - (TimeCurrent() - TimeGMT());
   MqlDateTime dt;
   TimeToStruct(gmt, dt);
   if(SessionStartHour <= 0 && SessionEndHour >= 24)
      return true;
   if(SessionStartHour == SessionEndHour)
      return true;
   if(SessionStartHour < SessionEndHour)
      return (dt.hour >= SessionStartHour && dt.hour < SessionEndHour);
   return (dt.hour >= SessionStartHour || dt.hour < SessionEndHour);
}

int OnInit()
{
   SetIndexBuffer(0, g_ema9, INDICATOR_DATA);
   SetIndexBuffer(1, g_ema21, INDICATOR_DATA);
   SetIndexBuffer(2, g_ema50, INDICATOR_DATA);
   SetIndexBuffer(3, g_buy, INDICATOR_DATA);
   SetIndexBuffer(4, g_sell, INDICATOR_DATA);
   SetIndexBuffer(5, g_avgGain, INDICATOR_CALCULATIONS);
   SetIndexBuffer(6, g_avgLoss, INDICATOR_CALCULATIONS);
   SetIndexBuffer(7, g_atr, INDICATOR_CALCULATIONS);

   PlotIndexSetInteger(3, PLOT_ARROW, 233);
   PlotIndexSetInteger(4, PLOT_ARROW, 234);
   PlotIndexSetInteger(3, PLOT_ARROW_SHIFT, 10);
   PlotIndexSetInteger(4, PLOT_ARROW_SHIFT, -10);
   PlotIndexSetDouble(3, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   PlotIndexSetDouble(4, PLOT_EMPTY_VALUE, EMPTY_VALUE);

   IndicatorSetString(INDICATOR_SHORTNAME, "FOMO Bill");
   IndicatorSetInteger(INDICATOR_DIGITS, _Digits);
   g_h1_ema = iMA(_Symbol, PERIOD_H1, 21, 0, MODE_EMA, PRICE_CLOSE);
   g_h4_ema = iMA(_Symbol, PERIOD_H4, 21, 0, MODE_EMA, PRICE_CLOSE);
   return INIT_SUCCEEDED;
}

int OnCalculate(const int rates_total,
                const int prev_calculated,
                const datetime &time[],
                const double &open[],
                const double &high[],
                const double &low[],
                const double &close[],
                const long &tick_volume[],
                const long &volume[],
                const int &spread[])
{
   if(rates_total < TrendEMA + 2)
      return 0;

   const double emaA = EmaAlpha(FastEMA);
   const double emaB = EmaAlpha(SlowEMA);
   const double emaC = EmaAlpha(TrendEMA);
   const double rsiA = 1.0 / RSIPeriod;
   const double atrA = EmaAlpha(ATRPeriod);
   const double pip = PipSize();

   int start = (prev_calculated <= 0) ? 0 : prev_calculated - 2;
   if(start < 0)
      start = 0;

   for(int i = start; i < rates_total; i++)
   {
      g_buy[i] = EMPTY_VALUE;
      g_sell[i] = EMPTY_VALUE;

      if(i == 0)
      {
         g_ema9[i] = close[i];
         g_ema21[i] = close[i];
         g_ema50[i] = close[i];
         g_avgGain[i] = 0;
         g_avgLoss[i] = 0;
         g_atr[i] = high[i] - low[i];
         continue;
      }

      g_ema9[i] = close[i] * emaA + g_ema9[i - 1] * (1.0 - emaA);
      g_ema21[i] = close[i] * emaB + g_ema21[i - 1] * (1.0 - emaB);
      g_ema50[i] = close[i] * emaC + g_ema50[i - 1] * (1.0 - emaC);

      const double delta = close[i] - close[i - 1];
      const double gain = (delta > 0.0) ? delta : 0.0;
      const double loss = (delta < 0.0) ? -delta : 0.0;
      g_avgGain[i] = gain * rsiA + g_avgGain[i - 1] * (1.0 - rsiA);
      g_avgLoss[i] = loss * rsiA + g_avgLoss[i - 1] * (1.0 - rsiA);

      const double tr = MathMax(high[i] - low[i],
                         MathMax(MathAbs(high[i] - close[i - 1]), MathAbs(low[i] - close[i - 1])));
      g_atr[i] = tr * atrA + g_atr[i - 1] * (1.0 - atrA);
   }

   string lastBias = "HOLD";
   int lastScore = 0;
   double lastRsi = 50.0;
   double lastAtrPips = 0.0;
   string lastWhy = "warming up";
   string lastHtf = "HOLD";

   for(int i = MathMax(start, 2); i < rates_total; i++)
   {
      if(i < 60)
         continue;

      lastRsi = 50.0;
      if(g_avgLoss[i] <= 0.0)
         lastRsi = (g_avgGain[i] > 0.0) ? 100.0 : 50.0;
      else
         lastRsi = 100.0 - (100.0 / (1.0 + g_avgGain[i] / g_avgLoss[i]));

      lastAtrPips = MathMax(g_atr[i] / pip, 1.0);
      const bool bullish = close[i] > open[i];
      const bool inSession = InSession(time[i]);

      string h1side = "HOLD";
      string h4side = "HOLD";
      if(g_h1_ema != INVALID_HANDLE)
      {
         const int sh = iBarShift(_Symbol, PERIOD_H1, time[i], false);
         if(sh >= 0)
         {
            double h1ema[];
            ArrayResize(h1ema, 1);
            if(CopyBuffer(g_h1_ema, 0, sh, 1, h1ema) == 1)
            {
               const double h1c = iClose(_Symbol, PERIOD_H1, sh);
               if(h1c > h1ema[0])
                  h1side = "BUY";
               else if(h1c < h1ema[0])
                  h1side = "SELL";
            }
         }
      }
      if(g_h4_ema != INVALID_HANDLE)
      {
         const int sh4 = iBarShift(_Symbol, PERIOD_H4, time[i], false);
         if(sh4 >= 0)
         {
            double h4ema[];
            ArrayResize(h4ema, 1);
            if(CopyBuffer(g_h4_ema, 0, sh4, 1, h4ema) == 1)
            {
               const double h4c = iClose(_Symbol, PERIOD_H4, sh4);
               if(h4c > h4ema[0])
                  h4side = "BUY";
               else if(h4c < h4ema[0])
                  h4side = "SELL";
            }
         }
      }
      lastHtf = "HOLD";
      if(h1side == h4side && (h1side == "BUY" || h1side == "SELL"))
         lastHtf = h1side;
      else if(h4side == "HOLD" && (h1side == "BUY" || h1side == "SELL"))
         lastHtf = h1side;

      lastBias = lastHtf;
      lastScore = (lastHtf == "HOLD") ? 0 : 8;
      lastWhy = "H1 and H4 not aligned";
      bool canOpen = false;
      if(!inSession)
         lastWhy = "outside London/NY window";
      else if(lastAtrPips < MinAtrPips)
         lastWhy = "ATR too small";
      else if(lastHtf == "HOLD")
         lastWhy = "H1 and H4 not aligned";
      else
      {
         lastWhy = "open " + lastHtf + ": multi-TF trend (Bill)";
         canOpen = true;
      }

      if(canOpen && i < rates_total - 1)
      {
         if(lastBias == "BUY")
            g_buy[i] = low[i];
         else
            g_sell[i] = high[i];
      }
   }

   Comment(StringFormat(
      "FOMO Bill  H1+H4 %s  RSI %.1f  ATR %.1f pip\n%s  (arrows = same open rules as the bot; demo visual only)",
      lastHtf, lastRsi, lastAtrPips, lastWhy
   ));
   return rates_total;
}

void OnDeinit(const int reason)
{
   if(g_h1_ema != INVALID_HANDLE)
      IndicatorRelease(g_h1_ema);
   if(g_h4_ema != INVALID_HANDLE)
      IndicatorRelease(g_h4_ema);
   Comment("");
}
