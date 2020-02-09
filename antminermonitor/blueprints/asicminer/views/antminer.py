import concurrent.futures
import re
import sys
import time
from datetime import timedelta

from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, url_for)
from flask_login import login_required
from sqlalchemy.exc import IntegrityError

from antminermonitor.blueprints.asicminer.asic_antminer import ASIC_ANTMINER
from antminermonitor.blueprints.asicminer.models import Miner
from antminermonitor.extensions import db
from config.settings import MODELS, NUM_THREADS
from lib.util_hashrate import update_unit_and_value

antminer = Blueprint('antminer', __name__, template_folder='../templates')


@antminer.route('/')
@login_required
def miners():
    # Init variables
    start = time.clock()
    miners = Miner.query.all()
    active_miners = []
    inactive_miners = []
    warnings = []
    errors = []
    total_hash_rate_per_model = {}
    miner_objects = []

    # lookup table for total_hash_rate_per_model
    for id, miner in MODELS.items():
        total_hash_rate_per_model[id] = {"value": 0, "unit": miner.get('unit')}

    # create miner objects to pass to executor.map
    for miner in miners:
        module = MODELS[miner.model_id]['model_module']
        cls = MODELS[miner.model_id]['model_classname']
        obj = getattr(sys.modules[module], cls)(miner)
        miner_objects.append(obj)

    # pass this method to executor.map to poll the miner
    def poll(obj):
        obj.poll()
        return obj

    # run with ThreadPoolExecutor
    with concurrent.futures.ThreadPoolExecutor(max_workers=NUM_THREADS) as executor:
        results = executor.map(poll, miner_objects)
        for miner in results:
            if miner is not None:
                if miner.is_inactive:
                    inactive_miners.append(miner)
                else:
                    active_miners.append(miner)

                    for warning in miner.warnings:
                        warnings.append(warning)
                    for error in miner.errors:
                        errors.append(error)
                    total_hash_rate_per_model[
                        miner.model_id]["value"] += miner.hash_rate_ghs5s

    # Flash notifications
    if not miners:
        error_message = ("[INFO] No miners added yet. "
                         "Please add miners using the above form.")
        current_app.logger.info(error_message)
        flash(error_message, "info")
    elif not errors:
        error_message = ("[INFO] All miners are operating normal. "
                         "No errors found.")
        current_app.logger.info(error_message)
        flash(error_message, "info")

    for error in errors:
        current_app.logger.error(error)
        flash(error, "error")

    for warning in warnings:
        current_app.logger.error(warning)
        flash(warning, "warning")

    # flash("[INFO] Check chips on your miner", "info")
    # flash("[SUCCESS] Miner added successfully", "success")
    # flash("[WARNING] Check temperatures on your miner", "warning")
    # flash("[ERROR] Check board(s) on your miner", "error")

    # Convert the total_hash_rate_per_model into a data structure that the
    # template can consume.
    total_hash_rate_per_model_temp = {}
    for key in total_hash_rate_per_model:
        value, unit = update_unit_and_value(
            total_hash_rate_per_model[key]["value"],
            total_hash_rate_per_model[key]["unit"])
        if value > 0:
            total_hash_rate_per_model_temp[key] = "{:3.2f} {}".format(
                value, unit)

    end = time.clock()
    loading_time = end - start
    return render_template(
        'asicminer/home.html',
        version=current_app.config['__VERSION__'],
        models=MODELS,
        errors=errors,
        warnings=warnings,
        active_miners=active_miners,
        inactive_miners=inactive_miners,
        loading_time=loading_time,
        total_hash_rate_per_model=total_hash_rate_per_model_temp)


@antminer.route('/add', methods=['POST'])
@login_required
def add_miner():
    miner_ip = request.form['ip']
    miner_model_id = request.form.get('model_id')
    miner_remarks = request.form['remarks']

    # exists = Miner.query.filter_by(ip="").first()
    # if exists:
    #    return "IP Address already added"

    try:
        miner = Miner(ip=miner_ip,
                      model_id=miner_model_id,
                      remarks=miner_remarks)
        db.session.add(miner)
        db.session.commit()
        flash("Miner with IP Address {} added successfully".format(miner.ip),
              "success")
    except IntegrityError:
        db.session.rollback()
        flash("IP Address {} already added".format(miner_ip), "error")

    return redirect(url_for('antminer.miners'))


@antminer.route('/delete/<id>')
@login_required
def delete_miner(id):
    miner = Miner.query.filter_by(id=int(id)).first()
    if miner:
        db.session.delete(miner)
        db.session.commit()
        flash("Miner {} removed successfully".format(miner.ip), "info")
    return redirect(url_for('antminer.miners'))
