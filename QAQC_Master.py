import configparser
import logging
import datetime as dt
import numpy as np
import pandas as pd
import sys
import math

import refet
from refet import calcs

from qaqc_modules import data_functions, input_functions
from qaqc_modules.correction import *
from qaqc_modules.rs_et_calc import emprso_w_tr

from bokeh.plotting import *
from bokeh.layouts import *

print("\nSystem: Starting data correction script.")

config_path = 'config.ini'  # Hardcoded for now, eventually will create function to pull config path from excel file

#########################
# Obtaining initial data
(data_df, column_df, station_name, station_lat, station_elev, ws_anemometer_height,
 missing_fill_value, script_mode, generate_bokeh) = input_functions.obtain_data(config_path)

print("\nSystem: Raw data successfully extracted from station file.")

# Extract individual variables from data frame back into to numpy arrays.
data_year = np.array(data_df.year)
data_month = np.array(data_df.month)
data_day = np.array(data_df.day)
data_tavg = np.array(data_df.tavg)
data_tmax = np.array(data_df.tmax)
data_tmin = np.array(data_df.tmin)
data_tdew = np.array(data_df.tdew)
data_ea = np.array(data_df.ea)
data_rhavg = np.array(data_df.rhavg)
data_rhmax = np.array(data_df.rhmax)
data_rhmin = np.array(data_df.rhmin)
data_rs = np.array(data_df.rs)
data_ws = np.array(data_df.ws)
data_precip = np.array(data_df.precip)


#########################
# Calculating secondary variables
print("\nSystem: Now calculating secondary variables based on data provided.")
data_length = data_year.shape[0]
station_pressure = 101.3 * (((293 - (0.0065 * station_elev)) / 293) ** 5.26)  # units kPa, EQ 3 in ASCE RefET manual

# Calculate DOY from Y/M/D values
data_doy = []
for i in range(data_length):
    data_doy.append(dt.date(data_year[i], data_month[i], data_day[i]).strftime("%j"))  # list of string DOY values

data_doy = np.array(list(map(int, data_doy)))  # Converts list of string vals into ints and saves as numpy array

# Figure out which humidity variables are provided and calculate Ea and TDew if needed
(data_ea, data_tdew) = data_functions.calc_humidity_variables(data_tmax, data_tmin, data_tavg, data_ea,
                                                              column_df.ea, data_tdew, column_df.tdew, data_rhmax,
                                                              column_df.rhmax, data_rhmin, column_df.rhmin,
                                                              data_rhavg, column_df.rhavg)

# Calculates secondary temperature values and mean monthly counterparts
(delta_t, mm_delta_t, k_not, mm_k_not, mm_tmin, mm_tdew) = data_functions.\
    calc_temperature_variables(data_month, data_tmax, data_tmin, data_tdew)

# Calculates rso and grass/alfalfa reference evapotranspiration from refet package
(rso, mm_rs, eto, etr, mm_eto, mm_etr) = data_functions.\
    calc_rso_and_refet(station_lat, station_elev, ws_anemometer_height, data_doy, data_month,
                       data_tmax, data_tmin, data_ea, data_ws, data_rs)

# Calculates thornton running solar radiation with original B coefficient values TODO: Add optimization to this function
(rs_tr, mm_rs_tr) = data_functions.calc_rs_tr(data_month, rso, delta_t, mm_delta_t)

#########################
# Back up original data
# Original data will be saved to output file
# Values are also used to generate delta values of corrected data - original data
# TODO: Make this a dataframe
orig_tavg = data_tavg
orig_tmax = data_tmax
orig_tmin = data_tmin
orig_tdew = data_tdew
orig_ea = data_ea
orig_rhavg = data_rhavg
orig_rhmax = data_rhmax
orig_rhmin = data_rhmin
orig_rs = data_rs
orig_ws = data_ws
orig_precip = data_precip
orig_rs_tr = rs_tr
orig_rso = rso
orig_etr = etr
orig_eto = eto

#########################
# Correcting Data
# TODO start chewing through this, make as condensed as possible
# TODO add handling for user picking humidity when only Tdew was pased
fill_tdew = np.zeros(data_length)  # Tracks which Tdew values have been filled
fill_ea = np.zeros(data_length)  # Tracks which ea datapoints are filled in with filled tdew
while script_mode == 1:
    reset_output()  # clears bokeh output, prevents ballooning file sizes
    print('\nPlease select which of the following variables you want to correct'
          '\n   Enter 1 for TMax and TMin.'
          '\n   Enter 2 for TMin and TDew.'
          '\n   Enter 3 for Windspeed.'
          '\n   Enter 4 for Rs.'
          '\n   Enter 5 for Humidity Measurements (Vapor Pressure, RHMax and RHmin, or RHAvg.)'
          '\n   Enter 6 for Precipitation.'
          '\n   Enter 7 to stop applying corrections.'
          )

    user = int(input("\nEnter your selection: "))
    loop = 1
    while loop:
        if 1 <= user <= 7:
            loop = 0
        else:
            print('Please enter a valid option.')
            user = int(input('Specify which variable you would like to correct: '))

    # Correcting Temperature data
    if user == 1 or user == 2:

        if user == 1:
            # Tmax and Tmin
            (data_tmax, data_tmin) = correction(station_name, log_file, data_tmax, 'TMax', data_tmin,
                                                'TMin', dt_array, data_month, data_year, 1)
        else:
            # Tmin and Tdew
            (data_tmin, data_tdew) = correction(station_name, log_file, data_tmin, 'TMin', data_tdew,
                                                'TDew', dt_array, data_month, data_year, 1)

        # Due to temperature being corrected, most secondary variables have to be corrected.
        # Code below is taken from the secondary variable generation section - @SVG
        print("\nSystem: Now recalculating secondary variables now that temperature has been corrected.")
        print(dt.datetime.now())

        ##########
        # Temperature Section
        # Figures secondary temperature values and mean monthly counterparts for use downstream
        # Generate tmax-tmin and tmin-tdew
        delta_t = []  # temperature difference between tmax and tmin
        tmin_tdew = []  # temperature difference between tmin and tdew
        mm_delta_t = []  # monthly averaged temperature difference, across all years, so 12 values
        monthly_tmin_tdew = []  # monthly averaged tmin-tdew difference, across all years, so 12 values
        for i in range(data_length):
            delta_t.append(data_tmax[i] - data_tmin[i])
            tmin_tdew.append(data_tmin[i] - data_tdew[i])

        delta_t = np.array(delta_t)
        tmin_tdew = np.array(tmin_tdew)

        # Create average monthly temperature and monthly tmin-tdew for downstream analysis
        k = 1
        while k <= 12:
            temp_indexes = [ex for ex, ind in enumerate(data_month) if ind == k]
            temp_indexes = np.array(temp_indexes)

            temp_value = np.nanmean(delta_t[temp_indexes])
            mm_delta_t.append(temp_value)

            temp_value = np.nanmean(tmin_tdew[temp_indexes])
            monthly_tmin_tdew.append(temp_value)
            k += 1
        mm_delta_t = np.array(mm_delta_t)
        monthly_tmin_tdew = np.array(monthly_tmin_tdew)

        # Fill in any missing tdew data with tmin - k0 curve. If tdew is calculated instead of provided this isn't
        # factored into downstream calculations, if this is run a second time then nothing occurs because tdew is filled
        for i in range(data_length):
            if np.isnan(data_tdew[i]):
                data_tdew[i] = data_tmin[i] - monthly_tmin_tdew[data_month[i] - 1]
                fill_tdew[i] = data_tdew[i]
            else:
                # Nothing is required to be done
                pass

        # Check to see if we were given vapor pressure, if not, then recalculate it from corrected/filled tdew
        if vappres_col == -1:  # Vapor pressure not given, have to approximate from tdew
            calc_ea = np.array(0.6108 * np.exp((17.27 * data_tdew) / (data_tdew + 237.3)))  # EQ 8, units kPa
        else:
            calc_ea = np.array(data_ea)

        # Now that we've recalculated ea, track which ea values were created from filled tdew
        for i in range(data_length):
            if fill_tdew[i] != 0:
                fill_ea[i] = calc_ea[i]
            else:
                # Nothing is required to be done
                pass

        ##########
        # Solar Radiation and Evapotranspiration Section
        # Now that secondary temperature variables are taken care of, we will calculate Rs variables and ETrs/ETos
        rso_d = np.empty(data_length)  # Clear sky solar radiation
        rs_tr = np.empty(data_length)  # Thornton-Running solar radiation approximation.
        calc_etos = np.empty(data_length)
        calc_etrs = np.empty(data_length)
        refet_ra = np.empty(data_length)
        refet_rso = np.empty(data_length)

        # Due to incorporating Open-ET/RefET package, I am copying some variables while changing their units
        # to satisfy what the RefET package requires
        # Rs EQ below from https://cliflo-niwa.niwa.co.nz/pls/niwp/wh.do_help?id=ls_rad
        refet_input_rs = np.array(data_rs * 0.0864)  # convert W/m2 to  MJ/m2, for refet module
        refet_input_lat = station_lat * (math.pi / 180.0)  # convert latitude into radians for refet module

        print("\nSystem: Now recalculating rs_tr, rso, ETos, and ETrs.")
        print(dt.datetime.now())
        for i in range(data_length):
            temp_month = data_month[i] - 1  # pulling the current month, shifted by 1 for index

            # Calculating Thornton-Running estimated solar radiation and Clear sky solar radiation
            (rso_d[i], rs_tr[i]) = emprso_w_tr(station_lat, station_pressure, calc_ea[i], data_doy[i],
                                               mm_delta_t[temp_month], delta_t[i])

            # Calculating ETo in mm using refET package
            calc_etos[i] = refet.Daily(tmin=data_tmin[i], tmax=data_tmax[i], ea=calc_ea[i], rs=refet_input_rs[i],
                                       uz=data_ws[i],
                                       zw=ws_anemometer_height, elev=station_elev, lat=station_lat,
                                       doy=data_doy[i], method='refet').eto()

            # Calculating ETr in mm using refET package
            calc_etrs[i] = refet.Daily(tmin=data_tmin[i], tmax=data_tmax[i], ea=calc_ea[i], rs=refet_input_rs[i],
                                       uz=data_ws[i],
                                       zw=ws_anemometer_height, elev=station_elev, lat=station_lat,
                                       doy=data_doy[i], method='refet').etr()

            refet_ra[i] = calcs._ra_daily(lat=refet_input_lat, doy=data_doy[i], method='refet')
            refet_rso[i] = calcs._rso_daily(ra=refet_ra[i], ea=calc_ea[i], pair=station_pressure, doy=data_doy[i],
                                            lat=refet_input_lat)

        # Convert rs_tr and rso to w/m2
        rso_d = refet_rso  # TODO this is a temporary measure, completely phase out the old rso calculations
        rso_d *= 11.574
        rs_tr *= 11.574

        ##########
        # Mean Monthly Generation
        # Create all other mean monthly values for downstream analysis
        monthly_calc_etos = []
        monthly_calc_etrs = []
        mm_tmin = []
        mm_tdew = []
        mm_rs_tr = []

        k = 1
        while k <= 12:
            temp_indexes = [ex for ex, ind in enumerate(data_month) if ind == k]
            temp_indexes = np.array(temp_indexes)

            temp_value = np.nanmean(calc_etos[temp_indexes])
            monthly_calc_etos.append(temp_value)

            temp_value = np.nanmean(calc_etrs[temp_indexes])
            monthly_calc_etrs.append(temp_value)

            temp_value = np.nanmean(data_tmin[temp_indexes])
            mm_tmin.append(temp_value)

            temp_value = np.nanmean(data_tdew[temp_indexes])
            mm_tdew.append(temp_value)

            temp_value = np.nanmean(rs_tr[temp_indexes])
            mm_rs_tr.append(temp_value)

            k += 1

        monthly_calc_etos = np.array(monthly_calc_etos)
        monthly_calc_etrs = np.array(monthly_calc_etrs)
        mm_tmin = np.array(mm_tmin)
        mm_tdew = np.array(mm_tdew)
        monthly_data_rs = np.array(monthly_data_rs)
        mm_rs_tr = np.array(mm_rs_tr)

        print("\nSystem: Done recalculating all secondary variables.")
        print(dt.datetime.now())

    # Correcting Windspeed
    elif user == 3:
        (data_ws, data_null) = correction(station_name, log_file, data_ws, 'Ws', data_null, 'NONE', dt_array, data_month,
                                          data_year, 4)

        # After correcting windspeed, we need to regenerate downstream variables.
        print("\nSystem: Now recalculating secondary variables now that wind speed has been corrected.")
        print(dt.datetime.now())

        # Code is again taken from @SVG section
        calc_etos = np.empty(data_length)
        calc_etrs = np.empty(data_length)

        # Due to incorporating Open-ET/RefET package, I am copying some variables while changing their units
        # to satisfy what the RefET package requires
        # Rs EQ below from https://cliflo-niwa.niwa.co.nz/pls/niwp/wh.do_help?id=ls_rad
        refet_rs = np.array(data_rs * 0.0864)  # convert W/m2 to  MJ/m2,

        print("\nSystem: Now recalculating ETos and ETrs.")
        print(dt.datetime.now())
        for i in range(data_length):
            temp_month = data_month[i] - 1  # pulling the current month, shifted by 1 for index

            # Calculating ETo in mm using refET package
            calc_etos[i] = refet.Daily(tmin=data_tmin[i], tmax=data_tmax[i], ea=calc_ea[i], rs=refet_rs[i],
                                       uz=data_ws[i],
                                       zw=ws_anemometer_height, elev=station_elev, lat=station_lat,
                                       doy=data_doy[i], method='refet').eto()

            # Calculating ETr in mm using refET package
            calc_etrs[i] = refet.Daily(tmin=data_tmin[i], tmax=data_tmax[i], ea=calc_ea[i], rs=refet_rs[i],
                                       uz=data_ws[i],
                                       zw=ws_anemometer_height, elev=station_elev, lat=station_lat,
                                       doy=data_doy[i], method='refet').etr()

        monthly_calc_etrs = []
        monthly_calc_etos = []

        k = 1
        while k <= 12:
            temp_indexes = [ex for ex, ind in enumerate(data_month) if ind == k]
            temp_indexes = np.array(temp_indexes)

            temp_value = np.nanmean(calc_etrs[temp_indexes])
            monthly_calc_etrs.append(temp_value)

            temp_value = np.nanmean(calc_etos[temp_indexes])
            monthly_calc_etos.append(temp_value)

            k += 1

        monthly_calc_etos = np.array(monthly_calc_etos)
        monthly_calc_etrs = np.array(monthly_calc_etrs)
        print("\nSystem: Done recalculating all secondary variables.")
        print(dt.datetime.now())

    # Solar radiation
    elif user == 4:
        (data_rs, data_null) = correction(station_name, log_file, data_rs, 'Rs', rso_d, 'Rso', dt_array, data_month, data_year, 3)

        print("\nSystem: Now recalculating secondary variables now that solar radiation has been corrected.")
        print(dt.datetime.now())

        # Code is again taken from @SVG section
        calc_etos = np.empty(data_length)
        calc_etrs = np.empty(data_length)

        # Due to incorporating Open-ET/RefET package, I am copying some variables while changing their units
        # to satisfy what the RefET package requires
        # Rs EQ below from https://cliflo-niwa.niwa.co.nz/pls/niwp/wh.do_help?id=ls_rad
        refet_input_rs = np.array(data_rs * 0.0864)  # convert W/m2 to  MJ/m2,

        print("\nSystem: Now recalculating ETos and ETrs.")
        print(dt.datetime.now())
        for i in range(data_length):
            temp_month = data_month[i] - 1  # pulling the current month, shifted by 1 for index

            # Calculating ETo in mm using refET package
            calc_etos[i] = refet.Daily(tmin=data_tmin[i], tmax=data_tmax[i], ea=calc_ea[i], rs=refet_input_rs[i],
                                       uz=data_ws[i],
                                       zw=ws_anemometer_height, elev=station_elev, lat=station_lat,
                                       doy=data_doy[i], method='refet').eto()

            # Calculating ETr in mm using refET package
            calc_etrs[i] = refet.Daily(tmin=data_tmin[i], tmax=data_tmax[i], ea=calc_ea[i], rs=refet_input_rs[i],
                                       uz=data_ws[i],
                                       zw=ws_anemometer_height, elev=station_elev, lat=station_lat,
                                       doy=data_doy[i], method='refet').etr()

        monthly_calc_etrs = []
        monthly_calc_etos = []
        monthly_data_rs = []

        k = 1
        while k <= 12:
            temp_indexes = [ex for ex, ind in enumerate(data_month) if ind == k]
            temp_indexes = np.array(temp_indexes)

            temp_value = np.nanmean(calc_etrs[temp_indexes])
            monthly_calc_etrs.append(temp_value)

            temp_value = np.nanmean(calc_etos[temp_indexes])
            monthly_calc_etos.append(temp_value)

            temp_value = np.nanmean(data_rs[temp_indexes])
            monthly_data_rs.append(temp_value)

            k += 1

        monthly_calc_etos = np.array(monthly_calc_etos)
        monthly_calc_etrs = np.array(monthly_calc_etrs)
        monthly_data_rs = np.array(monthly_data_rs)
        print("\nSystem: Done recalculating all secondary variables.")
        print(dt.datetime.now())

    # Humidity
    elif user == 5:
        if vappres_col != -1:  # Vapor Pressure exists
            (data_ea, data_null) = correction(station_name, log_file, data_ea, 'Vapor Pressure', data_null, 'NONE',
                                                   dt_array, data_month, data_year, 4)
        elif vappres_col == -1 and rhmax_col != -1 and rhmin_col != -1:  # RHMax and RHMin exist but no Ea
            (data_rhmax, data_rhmin) = correction(station_name, log_file, data_rhmax, 'RHMax', data_rhmin,
                                                  'RHMin', dt_array, data_month, data_year, 2)
        else:  # Have to use RH Avg to calculate Ea
            (data_rhavg, data_null) = correction(station_name, log_file, data_rhavg, 'RHAvg', data_null, 'NONE',
                                                 dt_array, data_month, data_year, 4)
        # TODO: Add if handle here for if someone picks this option when the only humidity option they have it TDew

        # Due to humidity being corrected, most secondary variables have to be corrected.
        # Code below is taken from the secondary variable generation section - @SVG
        print("\nSystem: Now recalculating secondary variables now that humidity has been corrected.")
        print(dt.datetime.now())

        ##########
        # Humidity Section
        # Figures out how to calculate both vapor pressure (ea) and dewpoint temperature (Tdew) if they are not provided
        calc_tdew = []  # dewpoint temperature
        # Create tdew if we don't have it, using the best variables provided
        if tdew_col == -1:  # We are not given TDew
            if vappres_col != -1:  # Vapor Pressure exists
                calc_ea = np.array(data_ea)
                for i in range(data_length):
                    # Calculate TDew using actual vapor pressure
                    # Below equation was taken from the book "Evapotranspiration: Principles and Applications for Water
                    # Management" by Goyal and Harmsen, and is equation 9 in chapter 13, page 320.
                    calc_tdew.append((116.91 + (237.3 * np.log(calc_ea[i]))) / (16.78 - np.log(calc_ea[i])))

            elif vappres_col == -1 and rhmax_col != -1 and rhmin_col != -1:  # RHmax and RHmin exist
                eo_tmax = []  # saturation vapor pressure based on tmax, units kPa, EQ 7 in ASCE RefET manual
                eo_tmin = []  # saturation vapor pressure based on tmin, units kPa, EQ 7 in ASCE RefET manual
                calc_ea = []  # actual vapor pressure, units kPa, EQ 11 in ASCE RefET manual
                for i in range(data_length):
                    # We first estimate Ea using RHMax and Min, and then use calculated EA to calculate TDew
                    eo_tmax.append(0.6108 * np.exp((17.27 * data_tmax[i]) / (data_tmax[i] + 237.3)))  # EQ 7
                    eo_tmin.append(0.6108 * np.exp((17.27 * data_tmin[i]) / (data_tmin[i] + 237.3)))  # EQ 7
                    calc_ea.append((eo_tmin[i] * (data_rhmax[i] / 100)) + (eo_tmax[i] * (data_rhmin[i] / 100)) / 2)
                    calc_tdew.append((116.91 + (237.3 * np.log(calc_ea[i]))) / (16.78 - np.log(calc_ea[i])))
            else:
                # Because we are not given actual Vapor Pressure or RHMax/RHmin, we use RH Avg to calculate Ea/Tdew
                eo_tavg = []  # saturation vapor pressure based on tavg, units kPa, EQ 7 in ASCE RefET manual
                calc_ea = []  # actual vapor pressure, units kPa, EQ 14 in ASCE RefET manual
                calc_tdew = []
                for i in range(data_length):
                    # Estimate Tdew using ea, actual vapor pressure
                    eo_tavg.append(0.6108 * np.exp((17.27 * data_tavg[i]) / (data_tavg[i] + 237.3)))  # EQ 7
                    calc_ea.append((data_rhavg[i] / 100) * eo_tavg[i])  # EQ 14
                    calc_tdew.append(
                        (116.91 + (237.3 * np.log(calc_ea[i]))) / (16.78 - np.log(calc_ea[i])))  # Cited above

            data_tdew = np.array(calc_tdew)
            calc_ea = np.array(calc_ea)

        else:
            # We are given tdew, so now we need to check for and calculate vapor pressure
            data_tdew = np.array(data_tdew)

            if vappres_col == -1:  # Vapor pressure not given, have to approximate from tdew
                calc_ea = np.array(0.6108 * np.exp((17.27 * data_tdew) / (data_tdew + 237.3)))  # EQ 8, units kPa
            else:
                # Vapor pressure and tdew were both provided so we don't need to calculate either.
                calc_ea = np.array(data_ea)

        ##########
        # Temperature Section
        # Figures secondary temperature values and mean monthly counterparts for use downstream
        # Generate tmax-tmin and tmin-tdew
        delta_t = []  # temperature difference between tmax and tmin
        tmin_tdew = []  # temperature difference between tmin and tdew
        mm_delta_t = []  # monthly averaged temperature difference, across all years, so 12 values
        monthly_tmin_tdew = []  # monthly averaged tmin-tdew difference, across all years, so 12 values
        for i in range(data_length):
            delta_t.append(data_tmax[i] - data_tmin[i])
            tmin_tdew.append(data_tmin[i] - data_tdew[i])

        delta_t = np.array(delta_t)
        tmin_tdew = np.array(tmin_tdew)
        # Create average monthly temperature and monthly tmin-tdew for downstream analysis
        k = 1
        while k <= 12:
            temp_indexes = [ex for ex, ind in enumerate(data_month) if ind == k]
            temp_indexes = np.array(temp_indexes)

            temp_value = np.nanmean(delta_t[temp_indexes])
            mm_delta_t.append(temp_value)

            temp_value = np.nanmean(tmin_tdew[temp_indexes])
            monthly_tmin_tdew.append(temp_value)
            k += 1
        # Convert into numpy arrays
        mm_delta_t = np.array(mm_delta_t)
        monthly_tmin_tdew = np.array(monthly_tmin_tdew)

        fill_tdew = np.zeros(data_length)  # Tracks tdew fills, resetting from above because tdew is being recalced
        # Fill in any missing tdew data with tmin - k0 curve.
        for i in range(data_length):
            if np.isnan(data_tdew[i]):
                data_tdew[i] = data_tmin[i] - monthly_tmin_tdew[data_month[i] - 1]
                fill_tdew[i] = data_tdew[i]
            else:
                # Nothing is required to be done
                pass

        # Fill in any missing ea data with now filled tdew
        for i in range(data_length):
            if np.isnan(calc_ea[i]):
                calc_ea[i] = np.array(0.6108 * np.exp((17.27 * data_tdew[i]) / (data_tdew[i] + 237.3)))  # EQ 8, kPa
                fill_ea[i] = calc_ea[i]
            else:
                # Nothing is required to be done
                pass

        ##########
        # Solar Radiation and Evapotranspiration Section
        # Now that secondary temperature variables are taken care of, we will calculate Rs variables and ETrs/ETos
        rso_d = np.empty(data_length)  # Clear sky solar radiation
        rs_tr = np.empty(data_length)  # Thornton-Running solar radiation approximation.
        calc_etos = np.empty(data_length)
        calc_etrs = np.empty(data_length)
        refet_ra = np.empty(data_length)
        refet_rso = np.empty(data_length)

        # Due to incorporating Open-ET/RefET package, I am copying some variables while changing their units to satisfy
        # what the RefET package requires
        # Rs EQ below from https://cliflo-niwa.niwa.co.nz/pls/niwp/wh.do_help?id=ls_rad
        refet_input_rs = np.array(data_rs * 0.0864)  # convert W/m2 to  MJ/m2, for refet module
        refet_input_lat = station_lat * (math.pi / 180.0)  # convert latitude into radians for refet module

        print("\nSystem: Now recalculating rs_tr, rso, ETos, and ETrs.")
        print(dt.datetime.now())
        for i in range(data_length):
            temp_month = data_month[i] - 1  # pulling the current month, shifted by 1 for index

            # Calculating Thornton-Running estimated solar radiation and Clear sky solar radiation
            (rso_d[i], rs_tr[i]) = emprso_w_tr(station_lat, station_pressure, calc_ea[i], data_doy[i],
                                               mm_delta_t[temp_month], delta_t[i])

            # Calculating ETo in mm using refET package
            calc_etos[i] = refet.Daily(tmin=data_tmin[i], tmax=data_tmax[i], ea=calc_ea[i], rs=refet_input_rs[i],
                                       uz=data_ws[i],
                                       zw=ws_anemometer_height, elev=station_elev, lat=station_lat,
                                       doy=data_doy[i], method='refet').eto()

            # Calculating ETr in mm using refET package
            calc_etrs[i] = refet.Daily(tmin=data_tmin[i], tmax=data_tmax[i], ea=calc_ea[i], rs=refet_input_rs[i],
                                       uz=data_ws[i],
                                       zw=ws_anemometer_height, elev=station_elev, lat=station_lat,
                                       doy=data_doy[i], method='refet').etr()

            refet_ra[i] = calcs._ra_daily(lat=refet_input_lat, doy=data_doy[i], method='refet')
            refet_rso[i] = calcs._rso_daily(ra=refet_ra[i], ea=calc_ea[i], pair=station_pressure, doy=data_doy[i],
                                            lat=refet_input_lat)

        # Convert rs_tr and rso to w/m2
        rso_d = refet_rso  # TODO this is a temporary measure, completely phase out the old rso calculations
        rso_d *= 11.574
        rs_tr *= 11.574

        ##########
        # Mean Monthly Generation
        # Create all other mean monthly values for downstream analysis
        print("\nSystem: Now recalculating mean monthly values for generated secondary variables.")
        print(dt.datetime.now())

        monthly_calc_etos = []
        monthly_calc_etrs = []
        mm_tmin = []
        mm_tdew = []
        monthly_data_rs = []
        mm_rs_tr = []
        mm_dt_array = []

        k = 1
        while k <= 12:
            temp_indexes = [ex for ex, ind in enumerate(data_month) if ind == k]
            temp_indexes = np.array(temp_indexes)

            temp_value = np.nanmean(calc_etos[temp_indexes])
            monthly_calc_etos.append(temp_value)

            temp_value = np.nanmean(calc_etrs[temp_indexes])
            monthly_calc_etrs.append(temp_value)

            temp_value = np.nanmean(data_tmin[temp_indexes])
            mm_tmin.append(temp_value)

            temp_value = np.nanmean(data_tdew[temp_indexes])
            mm_tdew.append(temp_value)

            temp_value = np.nanmean(data_rs[temp_indexes])
            monthly_data_rs.append(temp_value)

            temp_value = np.nanmean(rs_tr[temp_indexes])
            mm_rs_tr.append(temp_value)

            mm_dt_array.append(k)

            k += 1

        monthly_calc_etos = np.array(monthly_calc_etos)
        monthly_calc_etrs = np.array(monthly_calc_etrs)
        mm_tmin = np.array(mm_tmin)
        mm_tdew = np.array(mm_tdew)
        monthly_data_rs = np.array(monthly_data_rs)
        mm_rs_tr = np.array(mm_rs_tr)

        print("\nSystem: Done recalculating all secondary variables.")
        print(dt.datetime.now())

    # Precipitation
    elif user == 6:
        (data_precip, data_null) = correction(station_name, log_file, data_precip, 'Precip', data_null, 'NONE',
                                              dt_array, data_month, data_year, 4)

        monthly_data_precip = []

        k = 1
        while k <= 12:
            temp_indexes = [ex for ex, ind in enumerate(data_month) if ind == k]
            temp_indexes = np.array(temp_indexes)

            temp_value = np.nanmean(data_precip[temp_indexes])
            monthly_data_precip.append(temp_value)

            k += 1

    else:
        # user quits, set looping boolean to false
        print('\n System: Exiting corrections.')
        correction_bl = 0

# ######################################################################################################################
# Script mode section - @SMS
# This section figures out which method of correction the user wants to apply and then does so.
# First check to see if correction flag has been checked, if not, just display the data in bokeh and close.
# If correction has been selected, set looping variable to true.

# TODO: Isn't this backwards? shouldn't the order be "see if we want to correct, then gen fig?
# TODO: actually tackle the correction handling
# script_mode = data_config['MODES'].getboolean('script_mode')  # 0 for looking, 1 for correction
# corr_mode = data_config['MODES'].getboolean('correction_mode')  # 0 for manual, 1 for .ini presets
# disp_bokeh = data_config['MODES'].getboolean('bokeh_plots')  # 0 for not generating, 1 for showing
# rh_plot = data_config['DATA'].getboolean('vappress_rhplot_flag')  # 0 for not generating, 1 for showing
# correction_bl = 0  # flag for correction later on that gets changed by ini file preference
# ini_corr = 0  # flag for correction later on that gets changed by ini file preference
# Generate graph
if generate_bokeh:
    # Create variables that will be used by bokeh plot
    dt_array = []
    for i in range(data_length):
        dt_array.append(dt.datetime(data_year[i], data_month[i], data_day[i]))
    dt_array = np.array(dt_array, dtype=np.datetime64)
    mm_dt_array = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])
    data_null = np.empty(data_length) * np.nan

    x_size = 500
    y_size = 350

    if script_mode == 0:
        output_file(station_name + "_before_corrections_composite_graph.html")
    elif script_mode == 1:
        output_file(station_name + "_after_corrections_composite_graph.html")
    else:
        # Incorrect setup of script mode variable, raise an error
        raise ValueError('Incorrect parameters: script mode is not set to a valid option.')

    # Create bokeh figure out of individual subplots
    s1 = figure(
        width=x_size, height=y_size, x_axis_type="datetime",
        x_axis_label='Timestep', y_axis_label='Celsius', title="Tmax and Tmin",
        tools='pan, box_zoom, undo, reset, hover, save'
    )
    s1.line(dt_array, data_tmax, line_color="red", legend="Data TMax")
    s1.line(dt_array, data_tmin, line_color="blue", legend="Data TMin")
    s1.legend.location = "bottom_left"

    s2 = figure(
        x_range=s1.x_range,
        width=x_size, height=y_size, x_axis_type="datetime",
        x_axis_label='Timestep', y_axis_label='Celsius', title="Tmin and Tdew",
        tools='pan, box_zoom, undo, reset, hover, save'
    )
    s2.line(dt_array, data_tmin, line_color="blue", legend="Data TMin")
    s2.line(dt_array, data_tdew, line_color="black", legend="Data TDew")
    s2.legend.location = "bottom_left"

    s3 = figure(
        x_range=s1.x_range,
        width=x_size, height=y_size, x_axis_type="datetime",
        x_axis_label='Timestep', y_axis_label='m/s', title="Windspeed",
        tools='pan, box_zoom, undo, reset, hover, save'
    )
    s3.line(dt_array, data_ws, line_color="black", legend="Windspeed")
    s3.legend.location = "bottom_left"

    # Subplot 4 changes based on what variables are provided
    if column_df.ea != -1:  # Vapor pressure was provided
        s4 = figure(
            x_range=s1.x_range,
            width=x_size, height=y_size, x_axis_type="datetime",
            x_axis_label='Timestep', y_axis_label='kPa', title="Vapor Pressure",
            tools='pan, box_zoom, undo, reset, hover, save'
        )
        s4.line(dt_array, data_ea, line_color="black", legend="Vapor Pressure")
        s4.legend.location = "bottom_left"
    elif column_df.ea == -1 and column_df.tdew != -1:  # Tdew was provided, show calculated vapor pressure
        s4 = figure(
            x_range=s1.x_range,
            width=x_size, height=y_size, x_axis_type="datetime",
            x_axis_label='Timestep', y_axis_label='kPa', title="Calculated Vapor Pressure",
            tools='pan, box_zoom, undo, reset, hover, save'
        )
        s4.line(dt_array, data_ea, line_color="black", legend="Vapor Pressure")
        s4.legend.location = "bottom_left"
    elif column_df.ea == -1 and column_df.tdew == -1 and column_df.rhmax != -1 and column_df.rhmin != -1:  # RH max/min
        s4 = figure(
            x_range=s1.x_range,
            width=x_size, height=y_size, x_axis_type="datetime",
            x_axis_label='Timestep', y_axis_label='%', title="RH Max and Min",
            tools='pan, box_zoom, undo, reset, hover, save'
        )
        s4.line(dt_array, data_rhmax, line_color="black", legend="RH Max")
        s4.line(dt_array, data_rhmin, line_color="blue", legend="RH Min")
        s4.legend.location = "bottom_left"
    elif column_df.ea == -1 and column_df.tdew == -1 and column_df.rhmax == -1 and column_df.rhavg != -1:  # RHavg only
        s4 = figure(
            x_range=s1.x_range,
            width=x_size, height=y_size, x_axis_type="datetime",
            x_axis_label='Timestep', y_axis_label='%', title="RH Average",
            tools='pan, box_zoom, undo, reset, hover, save'
        )
        s4.line(dt_array, data_rhavg, line_color="black", legend="RH Avg")
        s4.legend.location = "bottom_left"
    else:
        # If an unsupported combination of humidity variables is present, raise a value error.
        raise ValueError('Bokeh figure generation encountered an unexpected combination of humidity inputs.')

    s5 = figure(
        x_range=s1.x_range,
        width=x_size, height=y_size, x_axis_type="datetime",
        x_axis_label='Timestep', y_axis_label='mm', title="Precipitation",
        tools='pan, box_zoom, undo, reset, hover, save'
    )
    s5.line(dt_array, data_precip, line_color="black", legend="Precipitation")
    s5.legend.location = "bottom_left"

    s6 = figure(
        x_range=s1.x_range,
        width=x_size, height=y_size, x_axis_type="datetime",
        x_axis_label='Timestep', y_axis_label='W/m2', title="Rs and Rso",
        tools='pan, box_zoom, undo, reset, hover, save'
    )
    s6.line(dt_array, data_rs, line_color="blue", legend="Rs")
    s6.line(dt_array, rso, line_color="black", legend="Rso")
    s6.legend.location = "bottom_left"

    s7 = figure(
        width=x_size, height=y_size,
        x_axis_label='Month', y_axis_label='W/m2', title="MM Rs and Rs TR",
        tools='pan, box_zoom, undo, reset, hover, save'
    )
    s7.line(mm_dt_array, mm_rs, line_color="blue", legend="MM Rs")
    s7.line(mm_dt_array, mm_rs_tr, line_color="black", legend="MM Rs TR")
    s7.legend.location = "bottom_left"

    s8 = figure(
        x_range=s7.x_range,
        width=x_size, height=y_size,
        x_axis_label='Month', y_axis_label='Celsius', title="MM Tmin and Tdew",
        tools='pan, box_zoom, undo, reset, hover, save'
    )
    s8.line(mm_dt_array, mm_tmin, line_color="blue", legend="MM Tmin")
    s8.line(mm_dt_array, mm_tdew, line_color="black", legend="MM Tdew")
    s8.legend.location = "bottom_left"

    s9 = figure(
        x_range=s7.x_range,
        width=x_size, height=y_size,
        x_axis_label='Month', y_axis_label='Celsius', title="MM Tmin - Tdew",
        tools='pan, box_zoom, undo, reset, hover, save'
    )
    s9.line(mm_dt_array, mm_k_not, line_color="black", legend="MM Tmin - Tdew")
    s9.legend.location = "bottom_left"

    if column_df.rhmax != -1 and column_df.rhmin != -1 and column_df.ea != -1:
        # If both ea and rhmax/rhmin are provided, generate a supplementary rhmax/min graph and save
        s10 = figure(
            x_range=s1.x_range,
            width=x_size, height=y_size, x_axis_type="datetime",
            x_axis_label='Timestep', y_axis_label='%', title="RHMax and Min",
            tools='pan, box_zoom, undo, reset, hover, save'
        )
        s10.line(dt_array, data_rhmax, line_color="black", legend="RH Max")
        s10.line(dt_array, data_rhmin, line_color="blue", legend="RH Min")
        s10.legend.location = "bottom_left"

        fig = gridplot([[s1, s2, s3], [s4, s5, s6], [s7, s8, s9], [s10]], toolbar_location="left")
        save(fig)
    else:
        # If there is no 10th plot to generate, save the regular 9
        fig = gridplot([[s1, s2, s3], [s4, s5, s6], [s7, s8, s9]], toolbar_location="left")
        save(fig)

    print("\nSystem: Bokeh figure displaying data before correction has been generated and saved in directory.")
    print(dt.datetime.now())

# ######################################################################################################################
# Save data to an output file, xls in this case so we can have a second sheet with differences
# Data is saved regardless of script mode given above.

# Create fill numpy arrays to show when data was filled
fill_tavg = np.zeros(data_length)
fill_tmax = np.zeros(data_length)
fill_tmin = np.zeros(data_length)

for i in range(data_length):
    # TAvg
    if (orig_tavg[i] == data_tavg[i]) or (np.isnan(orig_tavg[i]) and np.isnan(data_tavg[i])):
        # Nothing is required to be done
        pass
    else:
        fill_tavg[i] = data_tavg[i]
    # TMax
    if (orig_tmax[i] == data_tmax[i]) or (np.isnan(orig_tmax[i]) and np.isnan(data_tmax[i])):
        # Nothing is required to be done
        pass
    else:
        fill_tmax[i] = data_tmax[i]
    # TMin
    if (orig_tmin[i] == data_tmin[i]) or (np.isnan(orig_tmin[i]) and np.isnan(data_tmin[i])):
        # Nothing is required to be done
        pass
    else:
        fill_tmin[i] = data_tmin[i]

# Create corrected-original delta numpy arrays
diff_tavg = np.array(data_tavg - orig_tavg)
diff_tmax = np.array(data_tmax - orig_tmax)
diff_tmin = np.array(data_tmin - orig_tmin)
diff_tdew = np.array(data_tdew - orig_tdew)
diff_vappres = np.array(data_ea - orig_vappres)
diff_rhavg = np.array(data_rhavg - orig_rhavg)
diff_rhmax = np.array(data_rhmax - orig_rhmax)
diff_rhmin = np.array(data_rhmin - orig_rhmin)
diff_rs = np.array(data_rs - orig_rs)
diff_rs_tr = np.array(rs_tr - orig_rs_tr)
diff_rso = np.array(rso - orig_rso)
diff_ws = np.array(data_ws - orig_ws)
diff_precip = np.array(data_precip - orig_precip)
diff_etr = np.array(etr - orig_etr)
diff_eto = np.array(eto - orig_eto)


# Create any individually-requested output data
ws_2m = calcs._wind_height_adjust(uz=data_ws, zw=ws_anemometer_height)

# Create datetime for output dataframe
datetime_df = pd.DataFrame({'year': data_year, 'month': data_month, 'day': data_day})
datetime_df = pd.to_datetime(datetime_df[['month', 'day', 'year']])
# Create column sequence so pandas prints file in correct order
colseq = ['year', 'month', 'day', 'TAvg (C)', 'TMax (C)', 'TMin (C)', 'TDew (C)', 'Vapor Pres (kPa)',
          'RHAvg (%)', 'RHMax (%)', 'RHMin (%)', 'Rs (w/m2)', 'Rs_TR (w/m2)', 'Rso (w/m2)',
          'Windspeed (m/s)', 'Precip (mm)', 'Calc_ETr (mm)', 'Calc_ETo (mm)',
          'ws_2m (m/s)']

# Create output dataframe
outdata_df = pd.DataFrame({'date': datetime_df, 'year': data_year, 'month': data_month, 'day': data_day,
                           'TAvg (C)': data_tavg, 'TMax (C)': data_tmax, 'TMin (C)': data_tmin, 'TDew (C)': data_tdew,
                           'Vapor Pres (kPa)': data_ea, 'RHAvg (%)': data_rhavg, 'RHMax (%)': data_rhmax,
                           'RHMin (%)': data_rhmin, 'Rs (w/m2)': data_rs, 'Rs_TR (w/m2)': rs_tr, 'Rso (w/m2)': rso,
                           'Windspeed (m/s)': data_ws, 'Precip (mm)': data_precip, 'ETr (mm)': etr,
                           'ETo (mm)': eto, 'ws_2m (m/s)': ws_2m}, index=datetime_df)
# Creating difference dataframe to track amount of correction
diffdata_df = pd.DataFrame({'date': datetime_df, 'year': data_year, 'month': data_month, 'day': data_day,
                            'TAvg (C)': diff_tavg, 'TMax (C)': diff_tmax, 'TMin (C)': diff_tmin, 'TDew (C)': diff_tdew,
                            'Vapor Pres (kPa)': diff_vappres, 'RHAvg (%)': diff_rhavg, 'RHMax (%)': diff_rhmax,
                            'RHMin (%)': diff_rhmin, 'Rs (w/m2)': diff_rs, 'Rs_TR (w/m2)': diff_rs_tr,
                            'Rso (w/m2)': diff_rso, 'Windspeed (m/s)': diff_ws, 'Precip (mm)': diff_precip,
                            'ETr (mm)': diff_etr, 'ETo (mm)': diff_eto},
                           index=datetime_df)
# Creating a fill dataframe that tracks where missing data was filled in
filldata_df = pd.DataFrame({'date': datetime_df, 'year': data_year, 'month': data_month, 'day': data_day,
                            'TAvg (C)': fill_tavg, 'TMax (C)': fill_tmax, 'TMin (C)': fill_tmin,
                            'TDew (C)': fill_tdew, 'Vapor Pres (kPa)': fill_ea},
                           index=datetime_df)

outdata_df = outdata_df.reindex(columns=colseq)
diffdata_df = diffdata_df.reindex(columns=colseq)
filldata_df = filldata_df.reindex(columns=colseq)

# Open up pandas excel writer
outwriter = pd.ExcelWriter(station_name + "_output" + ".xlsx", engine='xlsxwriter')
# Convert data frames to xlsxwriter excel objects
outdata_df.to_excel(outwriter, sheet_name='Corrected Data', na_rep=missing_fill_value)
diffdata_df.to_excel(outwriter, sheet_name='Delta (Corr - Orig)', na_rep=missing_fill_value)
filldata_df.to_excel(outwriter, sheet_name='Fill', na_rep=missing_fill_value)
# Save output file
outwriter.save()

print("\nSystem: Ending script and closing log file.")
print(dt.datetime.now())

logger = open(log_file, 'a')
logger.write('The file has been successfully processed and output files saved at %s. \n' % dt.datetime.now().strftime(
                                                                                           "%Y-%m-%d %H:%M:%S"))
logger.close()
