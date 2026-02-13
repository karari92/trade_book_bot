


# ================= POSITION MANAGEMENT =================
# Auto-track positions based on signals
if aggressive_buy and aggressive_data['confidence'] >= 80:
    if not st.session_state.in_position:
        # Open LONG position
        st.session_state.in_position = True
        st.session_state.position_type = "BUY"
        st.session_state.entry_price = current_price
        st.session_state.entry_time = datetime.now()
        st.session_state.prev_exit_triggered = False
        
        st.success(f"📈 AUTO-TRACKED: LONG position at ${current_price:,.2f}")

elif aggressive_sell and aggressive_data['confidence'] >= 80:
    if not st.session_state.in_position:
        # Open SHORT position
        st.session_state.in_position = True
        st.session_state.position_type = "SELL"
        st.session_state.entry_price = current_price
        st.session_state.entry_time = datetime.now()
        st.session_state.prev_exit_triggered = False
        
        st.success(f"📉 AUTO-TRACKED: SHORT position at ${current_price:,.2f}")

# Manual position controls in sidebar
with st.sidebar:
    st.markdown("---")
    st.subheader("📊 Position Control")
    
    if st.session_state.in_position:
        entry = st.session_state.entry_price
        pos_type = st.session_state.position_type
        
        if entry > 0 and current_price > 0:
            if pos_type == "BUY":
                pnl = (current_price - entry) / entry * 100
            else:
                pnl = (entry - current_price) / entry * 100
            
            pnl_color = "green" if pnl > 0 else "red"
            st.markdown(f"""
            *Current Position:* {pos_type}  
            *Entry:* ${entry:,.2f}  
            *Current:* ${current_price:,.2f}  
            *PnL:* <span style='color:{pnl_color}'>{pnl:+.2f}%</span>
            """, unsafe_allow_html=True)
            
            if st.button("🚪 Close Position"):
                st.session_state.in_position = False
                st.session_state.position_type = None
                st.session_state.entry_price = 0.0
                st.session_state.prev_exit_triggered = False
                
                message = f"""
🚪 *POSITION CLOSED*
Type: {pos_type}
Entry: ${entry:,.2f}
Exit: ${current_price:,.2f}
PnL: {pnl:+.2f}%
"""
                send_telegram_alert(message)
                st.rerun()
    else:
        st.info("No active position")
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("📈 Track LONG"):
                st.session_state.in_position = True
                st.session_state.position_type = "BUY"
                st.session_state.entry_price = current_price
                st.session_state.entry_time = datetime.now()
                st.rerun()
        with col2:
            if st.button("📉 Track SHORT"):
                st.session_state.in_position = True
                st.session_state.position_type = "SELL"
                st.session_state.entry_price = current_price
                st.session_state.entry_time = datetime.now()
                st.rerun()