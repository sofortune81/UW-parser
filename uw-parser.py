import streamlit as st
import pandas as pd
import requests
import io
import matplotlib.pyplot as plt
#from config import DISCORD_WEBHOOK_URL  # Import from config.py

DISCORD_WEBHOOK_URL = st.secrets.get("DISCORD_WEBHOOK_URL")
if not DISCORD_WEBHOOK_URL:
    st.error("DISCORD_WEBHOOK_URL not set in secrets!")
    st.stop()

st.set_page_config(layout="wide")  # Force wide layout for full table width

st.title("Parse UW FLOW")
st.markdown("""
<style>
[data-testid="stFileUploaderDropzone"] {
    min-height: 500px !important;
    height: 500px !important;
}
</style>
""", unsafe_allow_html=True)

uploaded_file = st.file_uploader("Choose a CSV file", type="csv")
threshold = st.number_input("Premium/MC * 1,000,000 Threshold (minimum 1)", min_value=1.0, value=100.0)

if uploaded_file is not None:
    try:
        df = pd.read_csv(uploaded_file)
        df.columns = df.columns.str.strip().str.lower()

        required = ['underlying_symbol', 'type', 'premium', 'marketcap']
        missing_cols = [col for col in required if col not in df.columns]
        if missing_cols:
            st.error(f"Missing columns: {missing_cols}. Found: {list(df.columns)}")
            st.stop()

        # Map bid and ask columns (like in the HTML version)
        # Prefer ewma_nbbo_* if present, else use bid/ask if they exist
        df['bid'] = df.get('ewma_nbbo_bid', df.get('bid', pd.Series([None] * len(df), index=df.index, dtype=object)))
        df['ask'] = df.get('ewma_nbbo_ask', df.get('ask', pd.Series([None] * len(df), index=df.index, dtype=object)))

        # Optionally drop the original ewma columns to clean up (uncomment if desired)
        # df = df.drop(columns=['ewma_nbbo_bid', 'ewma_nbbo_ask'], errors='ignore')

        # Clean numeric columns with error handling
        df['premium'] = pd.to_numeric(df['premium'].astype(str).str.replace(',', ''), errors='coerce')
        df['marketcap'] = pd.to_numeric(df['marketcap'].astype(str).str.replace(',', ''), errors='coerce')

        # Drop rows with NaN in key columns
        df = df.dropna(subset=['premium', 'marketcap'])

        if df.empty:
            st.warning("No valid data after cleaning.")
            st.stop()

        # Group and calculate ratio (dynamic agg for optional columns)
        agg_dict = {
            'premium': 'sum',
            'marketcap': 'first',
        }
        optional_cols = ['date', 'time', 'side', 'strike', 'expiry', 'dte', 'bid', 'ask', 'price', 'underlying_price',
                         'size', 'volume']
        for col in optional_cols:
            if col in df.columns:
                agg_dict[col] = 'first'

        grouped = df.groupby(['underlying_symbol', 'type']).agg(agg_dict).reset_index()
        grouped['ratio'] = (grouped['premium'] / grouped['marketcap']) * 1000000
        filtered_df = grouped[grouped['ratio'] >= threshold].sort_values('ratio', ascending=False)

        if not filtered_df.empty:
            st.success(f"Found {len(filtered_df)} rows above threshold.")
            # Format ratio and underlying_price to 2 decimals before display
            display_df = filtered_df.copy()
            if 'ratio' in display_df.columns:
                display_df['ratio'] = display_df['ratio'].round(2)
            if 'underlying_price' in display_df.columns:
                display_df['underlying_price'] = display_df['underlying_price'].round(2)

            # Auto-fit column config: Use specific types for formatting, with longer widths
            col_config = {
                "underlying_symbol": st.column_config.TextColumn(width="long"),
                "type": st.column_config.TextColumn(width="short"),
                "premium": st.column_config.NumberColumn(width="medium", format="$%.2f"),
                "marketcap": st.column_config.NumberColumn(width="long", format="$%.0f"),
                "ratio": st.column_config.NumberColumn(width="medium", format="%.2f"),
                "date": st.column_config.TextColumn(width="medium"),
                "time": st.column_config.TextColumn(width="short"),
                "side": st.column_config.TextColumn(width="short"),
                "strike": st.column_config.NumberColumn(width="short", format="%.2f"),
                "expiry": st.column_config.TextColumn(width="long"),
                "dte": st.column_config.NumberColumn(width="short"),
                "bid": st.column_config.NumberColumn(width="short", format="%.2f"),
                "ask": st.column_config.NumberColumn(width="short", format="%.2f"),
                "price": st.column_config.NumberColumn(width="short", format="%.2f"),
                "underlying_price": st.column_config.NumberColumn(width="medium", format="$%.2f"),
                "size": st.column_config.NumberColumn(width="short"),
                "volume": st.column_config.NumberColumn(width="long"),
            }
            # Use a container to force full width
            container = st.container()
            with container:
                st.dataframe(
                    display_df,
                    use_container_width=True,  # Fit to container width
                    column_config=col_config,  # Auto-fit per column
                    hide_index=True,  # Cleaner look
                )

            if st.button("Send to Discord (Image + Excel)"):
                excel_buffer = io.BytesIO()
                with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
                    filtered_df.to_excel(writer, index=False, sheet_name='Filtered Data')
                excel_buffer.seek(0)
                excel_filename = "filtered_data.xlsx"

                # Generate table image with auto-fit columns and bold font
                fig, ax = plt.subplots(figsize=(20, max(6, len(filtered_df) * 0.4)))  # Even wider canvas
                ax.axis('tight')
                ax.axis('off')
                table = ax.table(cellText=filtered_df.values, colLabels=filtered_df.columns, cellLoc='center',
                                 loc='center')
                table.auto_set_font_size(False)
                table.set_fontsize(9)  # Slightly larger for boldness
                table.scale(1.2, 2.0)  # Wider cols, taller rows for better fit
                table.auto_set_column_width(col=list(range(len(filtered_df.columns))))  # Auto-fit widths

                # Make font bold for all cells (fixed loop)
                for key, cell in table.get_celld().items():
                    cell.get_text().set_fontweight('bold')

                if len(filtered_df) > 20:  # Optional: Warn for large tables
                    st.warning("Table has many rows (>20); image may be tall—consider splitting batches.")
                plt.tight_layout(pad=0.1)  # Tighter layout to reduce whitespace
                plt.savefig('table_image.png', bbox_inches='tight', dpi=200, facecolor='white',
                            edgecolor='none')  # Higher DPI for clarity
                plt.close()

                # Send to Discord
                with open('table_image.png', 'rb') as img_file:
                    files = {
                        'file1': (excel_filename, excel_buffer,
                                  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
                        'file2': ('table_image.png', img_file, 'image/png')
                    }
                    payload = {
                        'content': f'Alert! {len(filtered_df)} rows above threshold {threshold}. See attached Excel and table image.'}
                    response = requests.post(DISCORD_WEBHOOK_URL, data=payload, files=files)

                if response.ok:  # Handles 200 (with files) or 204 (text-only)
                    st.success("Sent to Discord successfully!")
                    # Clean up temp file
                    import os

                    os.remove('table_image.png')
                else:
                    st.error(f"Discord error: {response.status_code} - {response.text}")
        else:
            st.warning("No rows above threshold.")
    except Exception as e:
        st.error(f"Error processing file: {e}")
else:
    st.info("Upload a CSV to start.")