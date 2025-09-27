```python
import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import sqlite3
import requests
import re
import folium
from streamlit_folium import st_folium
from geopy.distance import geodesic

# Database setup
conn = sqlite3.connect('chainchaser.db', check_same_thread=False)
c = conn.cursor()

# Create tables if not exist
c.execute('''CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS reviews (id INTEGER PRIMARY KEY, username TEXT, course TEXT, rating INTEGER, comment TEXT, flagged TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS rounds (id INTEGER PRIMARY KEY, username TEXT, course TEXT, date TEXT, throws TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS courses (id INTEGER PRIMARY KEY, name TEXT UNIQUE, lat REAL, lon REAL, layout TEXT)''')

# Initialize Thorpe Park if not in database
c.execute("SELECT name FROM courses WHERE name='Thorpe Park'")
if not c.fetchone():
    thorpe_layout = [
        {'hole': i, 'tee': {'lat': 35.2058 + (i*0.0005), 'lon': -111.6574 + (i*0.0003)},
         'baskets': [
             {'id': 1, 'lat': 35.2058 + (i*0.0008), 'lon': -111.6574 + (i*0.0006), 'active': True},
             {'id': 2, 'lat': 35.2058 + (i*0.0012), 'lon': -111.6574 + (i*0.0009), 'active': False}
         ]} for i in range(1, 19)
    ]
    c.execute("INSERT INTO courses (name, lat, lon, layout) VALUES (?, ?, ?, ?)",
              ('Thorpe Park', 35.205856, -111.657357, str(thorpe_layout)))
    conn.commit()

conn.commit()

# Google Places API key
GOOGLE_API_KEY = 'AIzaSyA-pdS8ScVi6DwPBkr6SX_YhUAkwdWpeSo'

# Developer password (change this to your secret)
DEV_PASSWORD = 'dev123'

# Helper: Flag lost disc mentions
def flag_lost_disc(comment):
    lost_keywords = r'\blost\b.*\bdisc\b|\bdisc\b.*\blost\b|\blose\b.*\bdisc\b'
    if re.search(lost_keywords, comment, re.IGNORECASE):
        return "This seems like a lost disc issue—want replacement suggestions?"
    return None

# Helper: Get nearby places (retailers or courses)
def get_nearby_places(lat, lon, keywords=['disc golf', 'frisbee golf', 'disc golf course', 'disc golf park'], radius=20000):
    all_results = []
    for keyword in keywords:
        url = f"https://maps.googleapis.com/maps/api/place/nearbysearch/json?location={lat},{lon}&radius={radius}&keyword={keyword}&key={GOOGLE_API_KEY}"
        response = requests.get(url)
        if response.status_code == 200:
            results = response.json().get('results', [])
            filtered = [(place['name'], place['geometry']['location']['lat'], place['geometry']['location']['lng']) 
                        for place in results[:5] if any(term in place['name'].lower() for term in ['disc', 'golf', 'frisbee', 'park'])]
            all_results.extend(filtered)
    unique_results = list(set(all_results))[:10]
    return unique_results

# Helper: Calculate par based on distance (feet)
def calculate_par(distance):
    if distance < 250:
        return 3
    elif distance < 475:
        return 4
    elif distance < 675:
        return 5
    else:
        return 6

# App layout
st.title("ChainChaser: Improve Your DG Game & Connect with Community")

# Session state for user and tracking
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.username = None
    st.session_state.current_round = []
    st.session_state.start_pos = None
    st.session_state.map_radius = 20000
    st.session_state.developer_mode = False
    st.session_state.current_location = None

# Login/Signup
if not st.session_state.logged_in:
    tab1, tab2 = st.tabs(["Login", "Signup"])
    with tab1:
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        if st.button("Login"):
            c.execute("SELECT * FROM users WHERE username=? AND password=?", (username, password))
            if c.fetchone():
                st.session_state.logged_in = True
                st.session_state.username = username
                st.success("Logged in!")
                st.write(f"Debug: Logged in as {st.session_state.username}, state: {st.session_state.logged_in}")
            else:
                st.error("Invalid credentials.")
    with tab2:
        new_username = st.text_input("New Username")
        new_password = st.text_input("New Password", type="password")
        if st.button("Signup"):
            try:
                c.execute("INSERT INTO users VALUES (?, ?)", (new_username, new_password))
                conn.commit()
                st.success("Signed up! Now login.")
            except sqlite3.IntegrityError:
                st.error("Username taken.")
else:
    st.sidebar.write(f"Welcome, {st.session_state.username}!")
    if st.sidebar.button("Logout"):
        st.session_state.logged_in = False
        st.session_state.username = None
    page = st.sidebar.selectbox("Choose Feature", ["Map Courses", "Submit Review", "View Reviews", "Track Round", "Analytics", "Lost Disc Helper"])

    # Developer Mode toggle in sidebar
    st.sidebar.header("Developer Mode")
    dev_pw = st.sidebar.text_input("Enter Password to Enable Editing", type="password")
    if dev_pw == DEV_PASSWORD:
        st.session_state.developer_mode = True
        st.sidebar.success("Developer Mode Enabled!")
    else:
        st.session_state.developer_mode = False
        if dev_pw:
            st.sidebar.error("Incorrect Password")

    if page == "Map Courses":
        st.header("Map Your Disc Golf Course")
        # Get user location
        try:
            from streamlit_geolocation import streamlit_geolocation
            if st.button("Refresh Current Location"):
                location = streamlit_geolocation()
                if location and location['latitude']:
                    st.session_state.current_location = (location['latitude'], location['longitude'])
                    st.write(f"Your location: {st.session_state.current_location[0]:.4f}, {st.session_state.current_location[1]:.4f}")
                else:
                    st.session_state.current_location = None
                    st.info("Using default Flagstaff location (enable GPS for accuracy).")
            lat, lon = st.session_state.current_location or (35.1983, -111.6513)  # Default Flagstaff
        except Exception as e:
            st.error(f"Geolocation error: {str(e)}. Using default Flagstaff location.")
            lat, lon = 35.1983, -111.6513
            st.session_state.current_location = None

        # Fetch nearby courses
        courses = get_nearby_places(lat, lon, radius=st.session_state.map_radius)
        course_names = [name for name, _, _ in courses] + ["Custom Course"]
        selected_course = st.selectbox("Select a Course", course_names)
        
        # Get course coords
        if selected_course != "Custom Course":
            course_data = next((name, c_lat, c_lon) for name, c_lat, c_lon in courses if name == selected_course)
            course_name, course_lat, course_lon = course_data
        else:
            course_name = st.text_input("Enter Custom Course Name")
            course_lat = st.number_input("Course Latitude", value=35.1983, step=0.0001, format="%.4f")
            course_lon = st.number_input("Course Longitude", value=-111.6513, step=0.0001, format="%.4f")
        
        # Save course to DB (available to all, but editing layout is developer-only)
        if st.button("Save Course"):
            try:
                c.execute("INSERT OR REPLACE INTO courses (name, lat, lon, layout) VALUES (?, ?, ?, ?)", 
                          (course_name, course_lat, course_lon, str([])))
                conn.commit()
                st.success(f"Course {course_name} saved!")
            except Exception as e:
                st.error(f"Error saving course: {str(e)}")

        # Load layout (shared for all users)
        c.execute("SELECT layout FROM courses WHERE name=?", (course_name,))
        layout_result = c.fetchone()
        layout = eval(layout_result[0]) if layout_result and layout_result[0] else []
        st.write(f"Debug: Loaded layout for {course_name}: {layout}")

        # Developer Mode: Add/update hole layouts via GPS
        if st.session_state.developer_mode:
            st.subheader("Developer: Manage Hole Layouts")
            hole_num = st.number_input("Hole Number", min_value=1, step=1)
            point_type = st.selectbox("Point Type", ["Tee Pad", "Basket"])
            if st.button("Use Current GPS Location"):
                try:
                    gps_loc = streamlit_geolocation()
                    if gps_loc and gps_loc['latitude']:
                        clicked_lat, clicked_lon = gps_loc['latitude'], gps_loc['longitude']
                        st.write(f"GPS captured: {clicked_lat:.4f}, {clicked_lon:.4f}")
                        # Update or add to layout
                        updated = False
                        for hole in layout:
                            if hole['hole'] == hole_num:
                                if point_type == "Tee Pad":
                                    hole['tee'] = {'lat': clicked_lat, 'lon': clicked_lon}
                                else:
                                    basket_id = st.number_input("Basket ID (e.g., 1 for Basket 1)", min_value=1, step=1)
                                    for b in hole['baskets']:
                                        if b['id'] == basket_id:
                                            b['lat'] = clicked_lat
                                            b['lon'] = clicked_lon
                                            b['active'] = st.checkbox("Set as Active", value=b.get('active', False))
                                            updated = True
                                            break
                                    if not updated:
                                        hole['baskets'].append({'id': basket_id, 'lat': clicked_lat, 'lon': clicked_lon, 'active': st.checkbox("Set as Active", value=False)})
                                updated = True
                                break
                        if not updated:
                            if point_type == "Tee Pad":
                                layout.append({'hole': hole_num, 'tee': {'lat': clicked_lat, 'lon': clicked_lon}, 'baskets': []})
                            else:
                                basket_id = st.number_input("Basket ID (e.g., 1 for Basket 1)", min_value=1, step=1)
                                layout.append({'hole': hole_num, 'tee': None, 'baskets': [{'id': basket_id, 'lat': clicked_lat, 'lon': clicked_lon, 'active': st.checkbox("Set as Active", value=False)}]})
                        c.execute("UPDATE courses SET layout=? WHERE name=?", (str(layout), course_name))
                        conn.commit()
                        st.session_state.current_location = (clicked_lat, clicked_lon)  # Update live marker
                        st.success(f"{point_type} for Hole {hole_num} added/updated!")
                        st.rerun()
                    else:
                        st.error("Failed to get GPS location. Enable location services on your device.")
                except Exception as e:
                    st.error(f"GPS error: {str(e)}")

        # Map display (read-only for users, satellite view)
        m = folium.Map(location=[course_lat, course_lon], zoom_start=15, 
                       tiles='https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', 
                       attr='Google Satellite')
        folium.Marker([course_lat, course_lon], popup=course_name, icon=folium.Icon(color="green", icon="flag")).add_to(m)
        if st.session_state.current_location:
            folium.CircleMarker([st.session_state.current_location[0], st.session_state.current_location[1]], 
                               radius=5, color="red", fill=True, fill_color="red", popup="You Are Here").add_to(m)
        for hole in layout:
            if hole.get('tee'):
                folium.Marker([hole['tee']['lat'], hole['tee']['lon']], popup=f"Hole {hole['hole']} Tee", icon=folium.Icon(color="orange", icon="map-marker")).add_to(m)
            for basket in hole.get('baskets', []):
                color = "blue" if basket['active'] else "gray"
                folium.Marker([basket['lat'], basket['lon']], popup=f"Hole {hole['hole']} Basket {basket['id']} (Active: {basket['active']})", icon=folium.Icon(color=color, icon="map-marker")).add_to(m)
                if basket['active'] and hole.get('tee'):
                    dist = geodesic((hole['tee']['lat'], hole['tee']['lon']), (basket['lat'], basket['lon'])).feet
                    par = calculate_par(dist)
                    folium.PolyLine([(hole['tee']['lat'], hole['tee']['lon']), (basket['lat'], basket['lon'])], color="green", weight=2, popup=f"Par {par} ({dist:.0f} ft)").add_to(m)
        
        # Plot throws from latest round for this course (user-specific)
        c.execute("SELECT throws FROM rounds WHERE username=? AND course=? ORDER BY date DESC LIMIT 1", 
                  (st.session_state.username, course_name))
        round_data = c.fetchone()
        if round_data:
            try:
                throws = eval(round_data[0])
                for hole_num, hole_throws in enumerate(throws, 1):
                    for i, throw in enumerate(hole_throws):
                        folium.Marker([throw['start_lat'], throw['start_lon']], 
                                     popup=f"Hole {hole_num} Throw {i+1} Start", 
                                     icon=folium.Icon(color="purple", icon="play")).add_to(m)
                        folium.Marker([throw['end_lat'], throw['end_lon']], 
                                     popup=f"Hole {hole_num} Throw {i+1} Landing ({throw['distance']:.0f} ft)", 
                                     icon=folium.Icon(color="red", icon="stop")).add_to(m)
            except:
                pass

        st_folium(m, width=700, height=400)
        st.write("Course map loaded—track your throws or view layouts!")
        if not courses:
            st.info("No courses found nearby. Try broadening the search radius or adding a custom course. Popular spots in Flagstaff include: Thorpe Park (18 holes, city course with pines), McPherson Park (24 holes, wooded with elevation and views of San Francisco Peaks), Fort Tuthill County Park (18 holes, free public course in historic park), Northern Arizona University Campus (9 holes, campus-friendly), Little America Hotel (9 holes, resort-style), and Arizona Snowbowl (18 holes, mountain terrain with views—seasonal, closed until summer 2025). Check UDisc for maps and latest conditions.")
            if st.button("Broaden Search"):
                st.session_state.map_radius += 10000
                st.rerun()

    elif page == "Submit Review":
        st.header("Share a Course Review (Community Feedback)")
        with st.form("review_form"):
            course = st.text_input("Course Name")
            rating = st.slider("Rating", 1, 5)
            comment = st.text_area("Comment (Share tips for improvement!)")
            submitted = st.form_submit_button("Submit")
            if submitted:
                flag = flag_lost_disc(comment)
                c.execute("INSERT INTO reviews (username, course, rating, comment, flagged) VALUES (?, ?, ?, ?, ?)",
                          (st.session_state.username, course, rating, comment, flag))
                conn.commit()
                st.success("Review shared!")
                if flag:
                    try:
                        from streamlit_geolocation import streamlit_geolocation
                        location = streamlit_geolocation()
                        if location and location['latitude']:
                            retailers = get_nearby_places(location['latitude'], location['longitude'], ['disc golf retailer', 'disc golf shop'], radius=10000)
                            st.info("Nearby: " + " | ".join([name for name, _, _ in retailers]) if retailers else "None found—try Par 4 the Parks or local shops via UDisc.")
                        else:
                            st.info("Allow location access for retailer suggestions.")
                    except Exception as e:
                        st.error(f"Geolocation error: {str(e)}.")

    elif page == "View Reviews":
        st.header("Community Reviews")
        reviews_df = pd.read_sql_query("SELECT * FROM reviews", conn)
        st.dataframe(reviews_df)

    elif page == "Track Round":
        st.header("Track a Round (Auto GPS Throws)")
        # Course selector for layout integration
        c.execute("SELECT name, lat, lon FROM courses")
        saved_courses = [(row[0], row[1], row[2]) for row in c.fetchall()]
        st.write(f"Debug: Saved courses from DB: {saved_courses}")  # Debug
        try:
            from streamlit_geolocation import streamlit_geolocation
            location = streamlit_geolocation()
            if location and location['latitude']:
                nearby_courses = get_nearby_places(location['latitude'], location['longitude'])
                st.write(f"Debug: Nearby courses: {nearby_courses}")  # Debug
                nearby_names = [name for name, _, _ in nearby_courses]
                all_courses = list(set([name for name, _, _ in saved_courses] + nearby_names)) + ["Custom Course"]
                st.session_state.current_location = (location['latitude'], location['longitude'])
                st.write(f"Your location: {st.session_state.current_location[0]:.4f}, {st.session_state.current_location[1]:.4f}")
            else:
                all_courses = [name for name, _, _ in saved_courses] + ["Custom Course"]
                st.session_state.current_location = None
                st.info("Enable GPS for live location and nearby courses.")
        except Exception as e:
            all_courses = [name for name, _, _ in saved_courses] + ["Custom Course"]
            st.session_state.current_location = None
            st.error(f"Geolocation error: {str(e)}.")
        
        st.write(f"Debug: All courses for selection: {all_courses}")  # Debug
        selected_course = st.selectbox("Select Course (for layout)", all_courses)
        
        # Load layout for selected course
        c.execute("SELECT layout, lat, lon FROM courses WHERE name=?", (selected_course,))
        layout_result = c.fetchone()
        if layout_result:
            layout = eval(layout_result[0])
            course_lat, course_lon = layout_result[1], layout_result[2]
            st.success(f"Layout loaded for {selected_course}")
        else:
            layout = []
            course_lat, course_lon = 35.1983, -111.6513  # Default Flagstaff
            st.warning("No layout found—basic tracking only.")
        
        # Display layout map
        m = folium.Map(location=[course_lat, course_lon], zoom_start=15, 
                       tiles='https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', 
                       attr='Google Satellite')
        if st.session_state.current_location:
            folium.CircleMarker([st.session_state.current_location[0], st.session_state.current_location[1]], 
                               radius=5, color="red", fill=True, fill_color="red", popup="You Are Here").add_to(m)
        for hole in layout:
            if hole.get('tee'):
                folium.Marker([hole['tee']['lat'], hole['tee']['lon']], popup=f"Hole {hole['hole']} Tee", icon=folium.Icon(color="orange")).add_to(m)
            for basket in hole.get('baskets', []):
                color = "blue" if basket['active'] else "gray"
                folium.Marker([basket['lat'], basket['lon']], popup=f"Hole {hole['hole']} Basket {basket['id']}", icon=folium.Icon(color=color)).add_to(m)
                if basket['active'] and hole.get('tee'):
                    dist = geodesic((hole['tee']['lat'], hole['tee']['lon']), (basket['lat'], basket['lon'])).feet
                    par = calculate_par(dist)
                    folium.PolyLine([(hole['tee']['lat'], hole['tee']['lon']), (basket['lat'], basket['lon'])], color="green", popup=f"Par {par}").add_to(m)
        
        # Plot current round throws
        for hole_num, hole_throws in enumerate(st.session_state.current_round, 1):
            for i, throw in enumerate(hole_throws):
                folium.Marker([throw['start_lat'], throw['start_lon']], 
                             popup=f"Hole {hole_num} Throw {i+1} Start", 
                             icon=folium.Icon(color="purple")).add_to(m)
                folium.Marker([throw['end_lat'], throw['end_lon']], 
                             popup=f"Hole {hole_num} Throw {i+1} Landing ({throw['distance']:.0f} ft)", 
                             icon=folium.Icon(color="red")).add_to(m)
        
        if st.button("Refresh Current Location"):
            try:
                location = streamlit_geolocation()
                if location and location['latitude']:
                    st.session_state.current_location = (location['latitude'], location['longitude'])
                    st.rerun()
            except Exception as e:
                st.error(f"Geolocation error: {str(e)}.")
        
        st_folium(m, width=700, height=300)
        
        date = st.date_input("Date")
        if st.button("Start New Hole"):
            st.session_state.current_round.append([])
        for hole_num, hole_throws in enumerate(st.session_state.current_round, 1):
            st.subheader(f"Hole {hole_num}")
            if st.button(f"Mark Throw Start (Tee/Lie) - Hole {hole_num}"):
                try:
                    from streamlit_geolocation import streamlit_geolocation
                    start_loc = streamlit_geolocation()
                    if start_loc and start_loc['latitude']:
                        st.session_state.start_pos = (start_loc['latitude'], start_loc['longitude'])
                        st.write(f"Start marked: {st.session_state.start_pos}")
                        st.session_state.current_location = st.session_state.start_pos  # Update live marker
                        st.rerun()
                    else:
                        st.info("Allow location access to mark throw.")
                except Exception as e:
                    st.error(f"Geolocation error: {str(e)}.")
            if st.button(f"Mark Landing - Hole {hole_num}") and st.session_state.start_pos:
                try:
                    from streamlit_geolocation import streamlit_geolocation
                    end_loc = streamlit_geolocation()
                    if end_loc and end_loc['latitude']:
                        end_pos = (end_loc['latitude'], end_loc['longitude'])
                        distance = geodesic(st.session_state.start_pos, end_pos).feet
                        hole_throws.append({
                            'start_lat': st.session_state.start_pos[0], 'start_lon': st.session_state.start_pos[1],
                            'end_lat': end_pos[0], 'end_lon': end_pos[1], 'distance': distance
                        })
                        st.write(f"Throw distance: {distance:.0f} ft")
                        st.session_state.start_pos = end_pos
                        st.session_state.current_location = end_pos  # Update live marker
                        st.rerun()
                    else:
                        st.info("Allow location access to mark landing.")
                except Exception as e:
                    st.error(f"Geolocation error: {str(e)}.")
        if st.button("Finish Round & Log"):
            throws_str = str(st.session_state.current_round)
            c.execute("INSERT INTO rounds (username, course, date, throws) VALUES (?, ?, ?, ?)",
                      (st.session_state.username, selected_course, str(date), throws_str))
            conn.commit()
            st.success("Round logged! Check Analytics or Map Courses to see your throws.")
            st.session_state.current_round = []

    elif page == "Analytics":
        st.header("Your Game Improvement Analytics")
        rounds_df = pd.read_sql_query(f"SELECT * FROM rounds WHERE username='{st.session_state.username}'", conn)
        if not rounds_df.empty:
            all_distances = []
            for throws_str in rounds_df['throws']:
                try:
                    round_throws = eval(throws_str)
                    for hole in round_throws:
                        for throw in hole:
                            all_distances.append(throw['distance'])
                except:
                    pass
            if all_distances:
                avg_dist = sum(all_distances) / len(all_distances)
                st.subheader("Throw Distance Trends")
                fig, ax = plt.subplots()
                pd.Series(all_distances).plot(kind='hist', ax=ax)
                st.pyplot(fig)
                st.write(f"Average throw distance: {avg_dist:.0f} ft")
                if avg_dist < 200:
                    st.info("Tip: Work on form—aim for 250+ ft drives with field practice.")
                elif avg_dist < 300:
                    st.info("Tip: Solid! Focus on accuracy drills to shave strokes.")
                else:
                    st.info("Tip: Pro level—share tips in reviews to help the community!")
            else:
                st.info("Track rounds with throws to see stats.")
        else:
            st.info("Track some rounds first!")

    elif page == "Lost Disc Helper":
        st.header("Lost Disc? Find Replacements")
        try:
            from streamlit_geolocation import streamlit_geolocation
            location = streamlit_geolocation()
            if location and location['latitude']:
                retailers = get_nearby_places(location['latitude'], location['longitude'], ['disc golf retailer', 'disc golf shop'], radius=10000)
                st.info("Nearby: " + " | ".join([name for name, _, _ in retailers]) if retailers else "None found—try Par 4 the Parks or local shops via UDisc.")
            else:
                st.info("Allow location access for retailer suggestions.")
        except Exception as e:
            st.error(f"Geolocation error: {str(e)}.")

# Close DB
conn.close()
```